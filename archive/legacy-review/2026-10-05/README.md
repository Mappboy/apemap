# Legacy review implementation

Exact pre-cutover Git bytes of the CSV review helpers, historical loaders, matcher
and APH pipeline from commit `a66fa0d` are retained for issue 51's independent parity
checks. They are historical research artifacts, excluded from the active Python
package and quality tool configuration. Commit `c5158aa` supersedes this workflow
with the review decision log and replay projection.

Only the parity audit imports these snapshots explicitly. They do not participate
in normal ingestion or review writes.
