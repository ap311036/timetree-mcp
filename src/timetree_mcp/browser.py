"""Legacy ego-browser reader kept as an optional compatibility transport."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote

from .operations import to_epoch_millis
from .parser import normalize_event, query_events


RESULT_MARKER = "__TIMETREE_MCP_RESULT__"
TASK_SPACE_NAME = os.environ.get("TIMETREE_EGO_TASK_SPACE", "TimeTree readonly MCP")
WRITE_OPERATIONS = {
    "create_event", "update_event", "delete_event", "create_memo", "update_memo", "delete_memo",
    "add_event_comment", "update_event_comment", "delete_event_comment", "update_calendar_labels",
}


def parse_result_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if RESULT_MARKER not in line:
            continue
        payload = json.loads(line.split(RESULT_MARKER, 1)[1])
        if not isinstance(payload, dict):
            raise RuntimeError("ego-browser returned a non-object payload")
        return payload
    raise RuntimeError("ego-browser did not return a TimeTree result")


def run_ego_browser_script(script: str) -> str:
    completed = subprocess.run(
        ["ego-browser", "nodejs"],
        input=script,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or ["unknown ego-browser error"]
        raise RuntimeError(f"ego-browser failed: {detail[0]}")
    return completed.stdout + "\n" + completed.stderr


def _today_range() -> tuple[str, str]:
    today = datetime.now().astimezone().date()
    return today.isoformat(), (today + timedelta(days=7)).isoformat()


def _script_for(operation: str, arguments: dict[str, Any]) -> str:
    request = quote(json.dumps({"operation": operation, "arguments": arguments}, ensure_ascii=False), safe="")
    template = r'''const encodedRequest = "__REQUEST__";
const marker = "__MARKER__";
const needsAppPage = __NEEDS_APP__;

try {
  const task = await useOrCreateTaskSpace(__TASK_SPACE__);
  let tab = await ensureRealTab();
  if (!tab) tab = await openOrReuseTab("https://timetreeapp.com/", { wait: true, timeout: 30 });
  if (needsAppPage) {
    const info = await pageInfo();
    if (!String(info.url || "").includes("/calendars")) {
      await gotoAndWait("https://timetreeapp.com/", { wait: true, timeout: 30 });
      await waitForLoad({ timeout: 30 });
    }
  }

  const result = await js(String.raw`(async () => {
    const request = JSON.parse(decodeURIComponent("__REQUEST__"));
    const args = request.arguments || {};
    const apiHeaders = { "X-TimeTreeA": "web/2.1.0/en" };

    async function requestJson(path, init = {}, requiresCsrf = false) {
      const headers = { ...apiHeaders, ...(init.headers || {}) };
      if (requiresCsrf) {
        const token = document.querySelector('meta[name="csrf-token"]')?.content;
        if (!token) throw new Error("TimeTree CSRF token unavailable; login again");
        headers["X-CSRF-Token"] = token;
      }
      const response = await fetch(path, { ...init, headers, credentials: "include" });
      const text = await response.text();
      let body = text;
      try { body = JSON.parse(text); } catch {}
      return { status: response.status, url: response.url, body };
    }

    function unwrap(value) {
      let current = value;
      for (let i = 0; i < 5; i += 1) {
        if (typeof current === "string") {
          try { current = JSON.parse(current); } catch { break; }
        }
        if (current && Array.isArray(current.results) && current.results.length) {
          const first = current.results[0];
          current = first.body ?? first.result ?? first;
          continue;
        }
        if (current && current.body && typeof current.body === "object") {
          current = current.body;
          continue;
        }
        break;
      }
      return current;
    }

    function arrayAt(payload, keys) {
      for (const key of keys) if (Array.isArray(payload?.[key])) return payload[key];
      return [];
    }

    async function getCalendars() {
      const response = await requestJson("/api/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ops: [{ method: "get", url: "/v1/calendars", params: { since: 0 } }], sequential: true }),
      });
      const payload = unwrap(response.body);
      const source = arrayAt(payload, ["calendars", "data"]);
      const calendars = source.filter((calendar) => {
        const attributes = calendar?.attributes || calendar;
        const deactivatedAt = attributes?.deactivated_at;
        if (deactivatedAt == null) return true;
        if (typeof deactivatedAt !== "number" || !Number.isFinite(deactivatedAt)) {
          throw new Error("Invalid TimeTree calendar deactivated_at");
        }
        return deactivatedAt === 0;
      }).map((calendar) => {
        const attributes = calendar?.attributes || calendar;
        return {
          id: String(calendar?.id ?? attributes?.id ?? calendar?.calendar_id ?? ""),
          alias_code: attributes?.alias_code ?? calendar?.alias_code ?? null,
          name: attributes?.name ?? calendar?.name ?? attributes?.title ?? "",
        };
      }).filter((calendar) => calendar.id);
      return { response, calendars, authenticated: response.status >= 200 && response.status < 300 && !String(response.url || "").includes("/signin") };
    }

    function extractEvents(payload) {
      const unwrapped = unwrap(payload);
      if (Array.isArray(unwrapped)) return unwrapped;
      return arrayAt(unwrapped, ["events", "calendar_events", "data"]);
    }

    async function getEvents(calendar) {
      const events = [];
      let since = 0;
      for (let page = 0; page < 25; page += 1) {
        const response = await requestJson("/api/v1/calendar/" + encodeURIComponent(calendar.id) + "/events/sync?since=" + since);
        const payload = unwrap(response.body);
        const batch = extractEvents(payload);
        events.push(...batch.map((event) => ({ ...event, calendar_id: event?.calendar_id ?? calendar.id })));
        const nextSince = Number(payload?.since);
        if (!payload?.chunk || !Number.isFinite(nextSince) || nextSince <= since || batch.length === 0) break;
        since = nextSince;
      }
      return events;
    }

    async function selectedCalendars() {
      const calendarResult = await getCalendars();
      if (!calendarResult.authenticated) return calendarResult;
      const requestedId = args.calendar_id;
      const selected = requestedId ? calendarResult.calendars.filter((calendar) => calendar.id === String(requestedId)) : calendarResult.calendars;
      if (requestedId && selected.length === 0) throw new Error("Unknown TimeTree calendar_id: " + requestedId);
      return { ...calendarResult, selected };
    }

    function ok(response, action) {
      if (response.status < 200 || response.status >= 300) throw new Error(action + " failed with HTTP " + response.status);
      return unwrap(response.body);
    }

    const calendarResult = await selectedCalendars();
    if (!calendarResult.authenticated) return { authenticated: false, reason: "TimeTree login is required in the ego-browser task space.", url: location.href, status: calendarResult.response.status };

    if (request.operation === "status") return { authenticated: true, url: location.href, calendar_count: calendarResult.calendars.length };
    if (request.operation === "list_calendars") return { authenticated: true, calendars: calendarResult.calendars };
    if (request.operation === "list_events" || request.operation === "sync_events" || request.operation === "list_memos") {
      const events = (await Promise.all(calendarResult.selected.map(getEvents))).flat();
      return { authenticated: true, calendars: calendarResult.calendars, events };
    }
    if (request.operation === "list_event_comments") {
      const response = await requestJson("/api/v1/calendar/" + args.calendar_id + "/event/" + args.event_uuid + "/activities?since=0");
      return { authenticated: true, event_activities: arrayAt(unwrap(response.body), ["event_activities"]) };
    }
    if (request.operation === "get_calendar_labels") {
      const response = await requestJson("/api/v1/calendar/" + args.calendar_id + "/labels");
      return { authenticated: true, ...unwrap(response.body) };
    }
    if (request.operation === "get_calendar_members") {
      const response = await requestJson("/api/v2/calendars/" + args.calendar_id + "/users");
      return { authenticated: true, ...unwrap(response.body) };
    }
    if (request.operation === "get_calendar_virtual_members") {
      const response = await requestJson("/api/v1/calendars/" + args.calendar_id + "/virtual_users");
      return { authenticated: true, ...unwrap(response.body) };
    }
    if (request.operation === "update_calendar_labels") {
      const currentResponse = await requestJson("/api/v1/calendar/" + args.calendar_id + "/labels");
      const current = arrayAt(unwrap(currentResponse.body), ["calendar_labels"]);
      const updates = new Map(args.labels.map((label) => [Number(label.id), label]));
      const merged = current.map((label) => {
        const update = updates.get(Number(label.id)) || {};
        return { id: Number(label.id), name: update.name ?? label.name ?? "", color: update.color ?? label.color ?? label.default_color };
      });
      for (const [id, update] of updates) {
        if (!current.some((label) => Number(label.id) === id)) {
          if (update.color === undefined) throw new Error("New label " + id + " requires color");
          merged.push({ id, name: update.name ?? "", color: update.color });
        }
      }
      const response = await requestJson("/api/v1/calendar/" + args.calendar_id + "/labels", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ calendar_labels: merged }) }, true);
      return { authenticated: true, success: true, ...ok(response, "update_calendar_labels") };
    }

    const calendarId = args.calendar_id;
    if (request.operation === "create_event" || request.operation === "create_memo") {
      const response = await requestJson("/api/v1/calendar/" + calendarId + "/event", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args.payload) }, true);
      const body = ok(response, request.operation);
      return { authenticated: true, success: true, event: body?.event ?? body };
    }
    if (request.operation === "update_event" || request.operation === "update_memo") {
      const response = await requestJson("/api/v1/calendar/" + calendarId + "/event/" + args.event_uuid, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args.payload) }, true);
      const body = ok(response, request.operation);
      return { authenticated: true, success: true, event: body?.event ?? body };
    }
    if (request.operation === "delete_event" || request.operation === "delete_memo") {
      const response = await requestJson("/api/v1/calendar/" + calendarId + "/event/" + args.event_uuid, { method: "DELETE" }, true);
      ok(response, request.operation);
      return { authenticated: true, success: true, deleted_event_uuid: args.event_uuid };
    }
    if (request.operation === "add_event_comment") {
      const response = await requestJson("/api/v1/calendar/" + calendarId + "/event/" + args.event_uuid + "/activity", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: crypto.randomUUID().replaceAll("-", ""), attachment: { content: args.content }, silent: args.silent ?? true }) }, true);
      const body = ok(response, request.operation);
      return { authenticated: true, success: true, comment: body?.event_activity ?? body };
    }
    if (request.operation === "update_event_comment") {
      const response = await requestJson("/api/v1/calendar/" + calendarId + "/event/" + args.event_uuid + "/activity/" + args.comment_id, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ attachment: { content: args.content } }) }, true);
      const body = ok(response, request.operation);
      return { authenticated: true, success: true, comment: body?.event_activity ?? body };
    }
    if (request.operation === "delete_event_comment") {
      const response = await requestJson("/api/v1/calendar/" + calendarId + "/event/" + args.event_uuid + "/activity/" + args.comment_id, { method: "DELETE" }, true);
      ok(response, request.operation);
      return { authenticated: true, success: true, deleted_comment_id: args.comment_id };
    }
    throw new Error("Unsupported browser operation: " + request.operation);
  })()`);
  cliLog(marker + JSON.stringify(result));
} catch (error) {
  cliLog(marker + JSON.stringify({ error: String(error) }));
  process.exitCode = 1;
}
'''
    return (
        template.replace("__REQUEST__", request)
        .replace("__MARKER__", RESULT_MARKER)
        .replace("__NEEDS_APP__", json.dumps(operation in WRITE_OPERATIONS))
        .replace("__TASK_SPACE__", json.dumps(TASK_SPACE_NAME))
    )


class EgoBrowserReader:
    def __init__(self, runner: Callable[[str], str] | None = None):
        self.runner = runner or run_ego_browser_script
        self._lock = threading.RLock()

    def read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            payload = parse_result_line(self.runner(_script_for(operation, arguments)))
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            if payload.get("authenticated") is False:
                raise RuntimeError(str(payload.get("reason", "TimeTree login is required")))
            if operation in {"list_events", "sync_events", "list_memos"}:
                return self._format_events(operation, arguments, payload)
            if operation == "list_event_comments":
                return self._format_comments(payload, arguments)
            return payload

    @staticmethod
    def _format_events(operation: str, arguments: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        calendars = payload.get("calendars") or []
        names = {str(item.get("id")): item.get("name") for item in calendars}
        events = []
        skipped = 0
        for raw in payload.get("events") or []:
            try:
                calendar_id = raw.get("calendar_id")
                events.append(normalize_event(raw, names.get(str(calendar_id))))
            except (AttributeError, TypeError, ValueError):
                skipped += 1
        if operation == "sync_events":
            result = {"authenticated": True, "calendar_id": arguments.get("calendar_id"), "events": events}
            if skipped:
                result["skipped_unreadable_events"] = skipped
            return result
        if operation == "list_memos":
            memos = [event for event in events if event.get("category") == 2 and not event.get("deactivated_at")]
            if arguments.get("updated_after") is not None:
                cutoff = to_epoch_millis(arguments["updated_after"])
                memos = [event for event in memos if event.get("updated_at") and to_epoch_millis(event["updated_at"]) > cutoff]
            if arguments.get("limit") is not None:
                memos = memos[: arguments["limit"]]
            return {"calendar_id": str(arguments["calendar_id"]), "memos": memos, "count": len(memos)}
        start_date = arguments.get("start_date")
        end_date = arguments.get("end_date")
        if not start_date or not end_date:
            default_start, default_end = _today_range()
            start_date = start_date or default_start
            end_date = end_date or default_end
        filtered = query_events(events, start_date=start_date, end_date=end_date, calendar_id=arguments.get("calendar_id"), query=arguments.get("query"), limit=arguments.get("limit", 100))
        result = {"start_date": start_date, "end_date": end_date, "calendar_id": arguments.get("calendar_id"), "query": arguments.get("query"), "count": len(filtered), "events": filtered}
        if skipped:
            result["skipped_unreadable_events"] = skipped
        return result

    @staticmethod
    def _format_comments(payload: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        comments = []
        for activity in payload.get("event_activities") or []:
            if activity.get("type") != 0 or activity.get("deactivated_at"):
                continue
            attachment = activity.get("attachment") or {}
            comments.append({"id": activity.get("id"), "event_id": activity.get("event_id", arguments.get("event_uuid")), "calendar_id": activity.get("calendar_id", arguments.get("calendar_id")), "content": attachment.get("content", ""), "author_id": activity.get("author_id"), "created_at": activity.get("created_at"), "updated_at": activity.get("updated_at")})
        return {"calendar_id": arguments.get("calendar_id"), "event_uuid": arguments.get("event_uuid"), "comments": comments, "count": len(comments)}
