## Domain context

- **Dataset**: GitHub most-starred repositories (open API metadata).
- **Purpose**: model each repository with its owner, language, license, topics, popularity, and creation time.
- **Entities**: a Repository and its owning Account.
- **Notable columns**: `full_name` (the natural key, e.g. `owner/repo`), `owner.login`/`owner.type` (nested), `language` (enum), `license.spdx_id`/`license.name` (nested), `topics` (multi-valued array of strings), `stargazers_count`/`forks_count`/`open_issues_count`, `archived` (boolean as `true`/`false`), `created_at` (ISO dateTime), `html_url`.
- **Synonyms**: repo→repository/リポジトリ, stars→stargazers, topic→tag/トピック, spdx→license id.
