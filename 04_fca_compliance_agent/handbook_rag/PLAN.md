# FCA Handbook RAG: plan

Goal: a searchable FCA Handbook that an agent uses to assess GitHub pull requests. It borrows topic 03's pattern (one
sub-agent per section, checked quotes) but is a separate project. Written 25 November 2026.

## Status

| Piece | State |
|---|---|
| Snapshot format, index, hybrid search, agent tools, quote checks | Built and tested on fictional data |
| FCA API downloader | **Blocked**: needs the API docs (behind sign-in) and a token |
| Local embeddings (`bge-small-en-v1.5`) | Built and tested, offline too. 5 of 6 test queries find their rule in the top 3 of 12 fictional rules, so not yet evidence about real text |
| Gateway embeddings | Not used. On 25 November 2026 the gateway refused `text-embedding-3-small` (403, policy "Workshop Model Access") |
| MCP server over HTTP, and a client for the core agent | Built and tested, including parallel calls |
| PR compliance agent | Not started |
| Version check and refresh | Not started, see below |

## Decisions

| Decision | Choice |
|---|---|
| What a PR is | A GitHub pull request |
| Scope | SYSC, PRIN, COBS and the Glossary |
| How the agent uses the RAG | As MCP tools, over HTTP. It never opens Qdrant or the FCA API itself |
| Embeddings | Local model, so any demo PC can run it. It must be the same model for indexing and searching |
| Where the code lives | `04_fca_compliance_agent/handbook_rag`, run as a separate MCP server. Its packages are the `handbook-rag` extra |
| Tools | **Open.** Suggested: `search_fca_handbook` and `check_citation`. The core agent cannot use the current six yet |
| CONC (consumer credit) | **Open.** Suggested, because the core agent mostly cites it. The Consumer Credit Act itself is not in the Handbook |
| Qdrant storage | On-disk folder (`.qdrant`) on the demo laptop. No Qdrant Cloud or server for now |

### If we need to demo from a different PC

Set that PC up with the repo, its own `.env` and the embedding model (see the README). FCA credentials are not needed.
For the data, add one of these:

- **Qdrant Cloud (suggested):** put the cluster URL and key in that PC's `.env` as `QDRANT_URL` and `QDRANT_API_KEY`. A
  team member creates the account. Never share or commit the key.
- **Copied index (simplest):** copy the `.qdrant` folder across with the MCP server stopped, or rebuild it there with
  `scripts.ingest`.

All the options, without running our own Qdrant server:

| Option | Another PC needs | Note |
|---|---|---|
| A. Qdrant Cloud (suggested) | Repo, `QDRANT_URL` and `QDRANT_API_KEY` | Needs an account. Not tested. Text and query vectors leave the machine |
| B. On-disk folder (suggested backup) | Repo, and a copied folder or a rebuild | Works offline. One process at a time |
| C. Host the MCP server on a cloud machine | A web address | Needs HTTPS and access control first: the server has no login and only listens locally |
| D. Tunnel to one PC | A web address | That PC must stay on. Needs access control, as in C |

## Next: FCA API downloader

- [ ] Save the API docs or OpenAPI file in `docs/`, with no token in it.
- [ ] Map API responses to `utils/schema.py`, keeping the raw responses next to the snapshot.
- [ ] Cache, back off on rate limits, and resume after a failure.
- [ ] Only use the API. Site terms 35.3 ban scraping; terms 25 and 26 allow reuse under OGL v3.0 with attribution.
- [ ] Confirm what the API gives for effective dates, provision types, glossary links and future versions.
- [ ] Start with SYSC, PRIN, COBS and the Glossary (sourcebooks agreed; firm type to be confirmed).

### Credentials

- Put the FCA username and API token in `04_fca_compliance_agent/handbook_rag/.env.fca` (git-ignored). Never the website password.
- Only the downloader process may read that file. The agent and its tools must not load it, so do not put the token in
  the shared `.env`, which every agent loads.
- If an agent is ever given a shell or file access, run it where `.env` and `.env.fca` are not readable.
- Before the repo is shared, scan the commits for secrets.

## Next: retrieval quality, once real text is loaded

- [ ] 20 or more real queries with known rules. The known miss is "shipped to production with no QA": it finds the
  guidance XSYS 4.1.2G, but not its rule XSYS 4.1.1R.
- [ ] Try a separate search for rules (R), and following guidance to its rule with `expand_references`.
- [ ] Have the agent turn developer wording into regulatory wording before it searches.

## Later: version-check node and pulling the latest version

Why: changes follow FCA Board meetings. These are roughly monthly but not fixed, and each legal instrument sets its own
start date. A rule can therefore start on any day. The API is reported to serve current and future versions only, so a
version we do not save cannot be downloaded later.

A. Scheduled refresh, outside the agent (the only part that needs FCA credentials). Suggested: daily check.

1. Fetch the current and future versions into `data/snapshots/<date>/`. Never overwrite an old snapshot.
2. Compare with the last snapshot by citation, start date and text hash: added, changed, removed, future-dated.
3. If nothing changed, record the check time and stop. If the result looks wrong (for example over 10% removed, or any
   sourcebook empty), keep the live index and alert a person.
4. Build a new collection, run the tests and retrieval evals, then move the Qdrant alias. `store.build_index` already
   builds separately and only moves the alias on success.
   - Future change: embed only the changed provisions.
5. Keep the last few collections for rollback. Keep every snapshot, for audit.
6. Write a change report, and flag earlier PR assessments that cited a changed rule.

B. Version-check node, at the start of the PR agent. It needs no FCA credentials.

1. Call the `index_status` MCP tool for the snapshot date. It still needs the time of the last successful check.
2. Fresh (under 48 hours): continue. Stale: continue with a warning. Over 7 days, or no index: stop for a person.
   These limits are suggestions, for the team to agree.
3. Record the snapshot date and `as_of` date in the assessment, so it can be reproduced.
4. Use the planned release date as `as_of`, and also report cited rules that change within 90 days.

Open points: does the API offer a last-modified date, ETag or change list; what are the rate limits; does the server
version of Qdrant become necessary (local mode allows one process, so a refresh cannot run while an agent is open).

## Later: PR compliance agent

1. Load the PR (title, description, diff, linked issues). Treat all of it as untrusted text, never as instructions.
2. Split it into changes, and skip ones with no regulatory relevance, such as formatting.
3. One sub-agent per change searches the Handbook, reads full provisions, and checks definitions and exceptions.
4. Each finding has a verdict (compliant, potential breach, unclear, not applicable), citation, word-for-word quote,
   file and line. Guidance (G) alone can never be a breach.
5. Plain code calls the `check_citation` MCP tool for every citation. A person reviews potential breaches and unclear results.

Most Handbook rules bind the firm, not a line of code, so output is evidence for a reviewer, not legal advice.
Evaluation: 10 to 20 labelled PRs, with separate scores for retrieval and verdicts.
