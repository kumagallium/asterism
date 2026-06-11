## JSON: github-repos.json

- Records: 40 (iterator `$[*]`)
- Path: `../experiments/coverage-corpus/datasets/github-repos/source/github-repos.json`
- Reference style: dot-path leaf fields (e.g. `structure.spacegroup`) — emit `rml:referenceFormulation ql:JSONPath`, `rml:iterator "$[*]"`, and `rml:reference` with the dot-paths below.

### Columns

| name | type | non-null rate | distinct values | sample values |
|---|---|---|---|---|
| `full_name` | xsd:string | 100% | 40 | `codecrafters-io/build-your-own-x`, `sindresorhus/awesome`, `freeCodeCamp/freeCodeCamp` |
| `owner.login` | xsd:string | 100% | 40 | `codecrafters-io`, `sindresorhus`, `freeCodeCamp` |
| `owner.type` | xsd:string | 100% | 2 | `Organization`, `User`, `Organization` |
| `language` | xsd:string | 75% | 14 | `Markdown`, `TypeScript`, `Python` |
| `license.spdx_id` | xsd:string | 90% | 9 | `CC0-1.0`, `BSD-3-Clause`, `MIT` |
| `license.name` | xsd:string | 90% | 9 | `Creative Commons Zero v1.0 Universal`, `BSD 3-Clause "New" or "Revised" License`, `MIT License` |
| `topics` | json-array | 100% | 35 | `["awesome-list", "free", "programming", `, `["awesome", "awesome-list", "lists", "re`, `["careers", "certification", "community"` |
| `stargazers_count` | xsd:integer | 100% | 40 | `514083`, `474599`, `446598` |
| `forks_count` | xsd:integer | 100% | 40 | `48710`, `35368`, `44877` |
| `open_issues_count` | xsd:integer | 100% | 40 | `504`, `81`, `175` |
| `archived` | xsd:string | 100% | 1 | `false`, `false`, `false` |
| `created_at` | xsd:dateTime | 100% | 40 | `2018-05-09T12:03:18Z`, `2014-07-11T13:42:37Z`, `2014-12-24T17:49:19Z` |
| `html_url` | xsd:string | 100% | 40 | `https://github.com/codecrafters-io/build`, `https://github.com/sindresorhus/awesome`, `https://github.com/freeCodeCamp/freeCode` |

### JSON columns

- `topics` (array of string)

### Uniqueness (★ trap T1 from workflow §6)

| key | rows considered | distinct | collisions | unique? |
|---|---|---|---|---|
| (full_name) | 40 | 40 | 0 | ✓ |
| (owner.login, full_name) | 40 | 40 | 0 | ✓ |
| (license.spdx_id, full_name) | 36 | 36 | 0 | ✓ |
| (stargazers_count, full_name) | 40 | 40 | 0 | ✓ |
| (forks_count, full_name) | 40 | 40 | 0 | ✓ |
| (open_issues_count, full_name) | 40 | 40 | 0 | ✓ |
| (created_at, full_name) | 40 | 40 | 0 | ✓ |
| (html_url, full_name) | 40 | 40 | 0 | ✓ |
| (owner.login, license.spdx_id, full_name) | 36 | 36 | 0 | ✓ |
| (owner.login, stargazers_count, full_name) | 40 | 40 | 0 | ✓ |
| (owner.login, forks_count, full_name) | 40 | 40 | 0 | ✓ |
| (owner.login, open_issues_count, full_name) | 40 | 40 | 0 | ✓ |
| (owner.login, created_at, full_name) | 40 | 40 | 0 | ✓ |
| (owner.login, html_url, full_name) | 40 | 40 | 0 | ✓ |
| (license.spdx_id, stargazers_count, full_name) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, forks_count, full_name) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, open_issues_count, full_name) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, created_at, full_name) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, html_url, full_name) | 36 | 36 | 0 | ✓ |
| (stargazers_count, forks_count, full_name) | 40 | 40 | 0 | ✓ |
| (stargazers_count, open_issues_count, full_name) | 40 | 40 | 0 | ✓ |
| (stargazers_count, created_at, full_name) | 40 | 40 | 0 | ✓ |
| (stargazers_count, html_url, full_name) | 40 | 40 | 0 | ✓ |
| (forks_count, open_issues_count, full_name) | 40 | 40 | 0 | ✓ |
| (forks_count, created_at, full_name) | 40 | 40 | 0 | ✓ |
| (forks_count, html_url, full_name) | 40 | 40 | 0 | ✓ |
| (open_issues_count, created_at, full_name) | 40 | 40 | 0 | ✓ |
| (open_issues_count, html_url, full_name) | 40 | 40 | 0 | ✓ |
| (created_at, html_url, full_name) | 40 | 40 | 0 | ✓ |
| (owner.login) | 40 | 40 | 0 | ✓ |
| (license.spdx_id, owner.login) | 36 | 36 | 0 | ✓ |
| (stargazers_count, owner.login) | 40 | 40 | 0 | ✓ |
| (forks_count, owner.login) | 40 | 40 | 0 | ✓ |
| (open_issues_count, owner.login) | 40 | 40 | 0 | ✓ |
| (created_at, owner.login) | 40 | 40 | 0 | ✓ |
| (html_url, owner.login) | 40 | 40 | 0 | ✓ |
| (license.spdx_id, stargazers_count, owner.login) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, forks_count, owner.login) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, open_issues_count, owner.login) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, created_at, owner.login) | 36 | 36 | 0 | ✓ |
| (license.spdx_id, html_url, owner.login) | 36 | 36 | 0 | ✓ |
| (stargazers_count, forks_count, owner.login) | 40 | 40 | 0 | ✓ |
| (stargazers_count, open_issues_count, owner.login) | 40 | 40 | 0 | ✓ |
| (stargazers_count, created_at, owner.login) | 40 | 40 | 0 | ✓ |
| (stargazers_count, html_url, owner.login) | 40 | 40 | 0 | ✓ |
| (forks_count, open_issues_count, owner.login) | 40 | 40 | 0 | ✓ |
| (forks_count, created_at, owner.login) | 40 | 40 | 0 | ✓ |
| (forks_count, html_url, owner.login) | 40 | 40 | 0 | ✓ |
| (open_issues_count, created_at, owner.login) | 40 | 40 | 0 | ✓ |
| (open_issues_count, html_url, owner.login) | 40 | 40 | 0 | ✓ |
| (created_at, html_url, owner.login) | 40 | 40 | 0 | ✓ |
| (license.spdx_id) | 36 | 9 | 27 | ✗ |
| (stargazers_count, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (forks_count, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (open_issues_count, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (created_at, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (html_url, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (stargazers_count, forks_count, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (stargazers_count, open_issues_count, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (stargazers_count, created_at, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (stargazers_count, html_url, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (forks_count, open_issues_count, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (forks_count, created_at, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (forks_count, html_url, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (open_issues_count, created_at, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (open_issues_count, html_url, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (created_at, html_url, license.spdx_id) | 36 | 36 | 0 | ✓ |
| (stargazers_count) | 40 | 40 | 0 | ✓ |
| (forks_count, stargazers_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count, stargazers_count) | 40 | 40 | 0 | ✓ |
| (created_at, stargazers_count) | 40 | 40 | 0 | ✓ |
| (html_url, stargazers_count) | 40 | 40 | 0 | ✓ |
| (forks_count, open_issues_count, stargazers_count) | 40 | 40 | 0 | ✓ |
| (forks_count, created_at, stargazers_count) | 40 | 40 | 0 | ✓ |
| (forks_count, html_url, stargazers_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count, created_at, stargazers_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count, html_url, stargazers_count) | 40 | 40 | 0 | ✓ |
| (created_at, html_url, stargazers_count) | 40 | 40 | 0 | ✓ |
| (forks_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count, forks_count) | 40 | 40 | 0 | ✓ |
| (created_at, forks_count) | 40 | 40 | 0 | ✓ |
| (html_url, forks_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count, created_at, forks_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count, html_url, forks_count) | 40 | 40 | 0 | ✓ |
| (created_at, html_url, forks_count) | 40 | 40 | 0 | ✓ |
| (open_issues_count) | 40 | 40 | 0 | ✓ |
| (created_at, open_issues_count) | 40 | 40 | 0 | ✓ |
| (html_url, open_issues_count) | 40 | 40 | 0 | ✓ |
| (created_at, html_url, open_issues_count) | 40 | 40 | 0 | ✓ |
| (created_at) | 40 | 40 | 0 | ✓ |
| (html_url, created_at) | 40 | 40 | 0 | ✓ |
| (html_url) | 40 | 40 | 0 | ✓ |

