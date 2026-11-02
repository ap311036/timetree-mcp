# TimeTree calendar monitoring

Terms used to distinguish calendar content, recurrence projections, and change notifications.

## Language

**Source record**: An event stored by TimeTree, including a recurring-series parent or a detached exception. A source record is not the same as each dated occurrence of that series.
_Avoid_: Occurrence when referring to a stored parent

**Occurrence**: One dated instance of an event, either generated from a recurrence rule or represented by an actual detached record. A generated occurrence identifier does not authorize a source-record edit.
_Avoid_: Source UUID when referring to a generated occurrence identifier

**Successful baseline**: The last complete and validated calendar snapshot accepted for comparison. A failed or partial read is never a new baseline.
_Avoid_: Latest read

**Pending notification**: A stable batch of detected changes whose notification has not completed a confirmed local transport write. An uncertain notification remains pending and does not authorize automatic resending.
_Avoid_: Undelivered message, because actual client receipt may be unknown
