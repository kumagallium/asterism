## Domain context

- **Dataset**: OpenLibrary search results (books) — public-domain bibliographic metadata.
- **Purpose**: model each book/work with its title, authors, languages, subjects, ISBNs, and first publication year.
- **Entities**: a Book (a Work).
- **Notable columns**: `title`, `author_name` (multi-valued array of strings), `isbn` (array), `language` (array of ISO-639 codes, often multi), `subject` (array of subject headings), `first_publish_year`, `number_of_pages_median`.
- **Synonyms**: author→creator/著者, subject→topic/件名, isbn→identifier.
