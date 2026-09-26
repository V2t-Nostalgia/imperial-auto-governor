# Etc Application

`etc` is the explicit staging owner for a native action whose final semantic
domain has not been reviewed yet. A runtime action descriptor may use
`application_id = "etc"`; its name, version, risk, verification state and
strict parameters then appear in the Broker tool catalog automatically.

This staging Application is catalog-only: it intentionally has no autonomous
conversation agent and cannot act merely because a runtime advertises a tool.
A tool still needs a
fresh snapshot, candidate identity, player grant and Broker permission before
execution. Moving a tool from `etc` to a permanent Application is a metadata
and policy change; it does not require editing the native hook or dispatcher.
