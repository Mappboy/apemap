# Reviewed dataset 0.7.0

The fresh [recipe](../../../data/release-recipes/reviewed-0.7.0.json) pins
package **0.15.0**, the current append-only review decisions and retained evidence,
and the existing immutable historical inputs. It preserves the historical
`reviewed-0.6.1.json` recipe and all prior artifacts.

Dataset **0.7.0** is a MINOR increment above the retained 0.6.1 candidate for the
nullable home-education contract while at 0.x. The previous published dataset is
0.3.3. Package and dataset versions are independent; this recipe-only change
does not bump package 0.15.0. Web and analysis contracts remain 2.0.0.

`Home educated` assertions retain their recorded text, source URL, retrieval
time, confidence and evidence with `institution_id = NULL` and
`institution_resolution = "home_educated"`. Attendance context retains these
rows with `identity_basis = "non_institution"` and no attended school identity.
School maps and school-specific analyses retain their institutional scope.
Jordon Steele-John's reviewed assertion is included without inventing a school.

Publication uses the existing dataset release workflow from the final merged
recipe commit. Build twice into fresh database/output paths, compare deterministic
outputs, strictly verify, inspect the diff against 0.3.3, and package before
dispatch. The workflow repeats the deterministic build and strict verification.
Download and strictly verify the published archive after publication. This record
does not claim those later checks have already run.
