## Domain context

- **Dataset**: Crossref scholarly works metadata sample (open metadata).
- **Purpose**: model each publication with its DOI, title, authors, venue, and date.
- **Entities**: a scholarly Work and its Authors.
- **Notable columns**: `DOI` (bare DOI, e.g. `10.1007/...`), `title` (array), `author` (array of {given, family, ...} objects — multi-valued), `published.date-parts` (nested `[[YYYY, M, D]]`), `container-title` (array), `type`, `is-referenced-by-count`.
- **Synonyms**: DOI→digital object identifier, author→creator/著者, container-title→journal/venue/掲載誌.
