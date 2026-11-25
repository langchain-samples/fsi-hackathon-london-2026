# FCA Handbook RAG: plan

Goal: a searchable FCA Handbook that an agent uses to assess GitHub pull requests. It borrows topic 03's pattern (one
sub-agent per section, checked quotes) but is a separate project. Written 25 November 2026.

## Status

| Piece | State |
|---|---|
| Snapshot format, index, hybrid search, agent tools, quote checks | Built and tested on fictional data |
| FCA API downloader | Built and tested. 130 chapters on 25 November 2026, every provision identical to the FCA's text |
| Real FCA text | Embedded on one laptop: 4,679 provisions and 3,733 terms. Not in git |
| Local embeddings (`bge-small-en-v1.5`) | Built and tested, offline too. 5 of 6 test queries find their rule in the top 3 of 12 fictional rules, so not yet evidence about real text |
| Gateway embeddings | Not used. On 25 November 2026 the gateway refused `text-embedding-3-small` (403, policy "Workshop Model Access") |
| MCP server over HTTP with three tools | Built and tested with the core agent's adapter, including parallel calls |
| Reviewing agent (FCA and CCA) | **Placeholder.** Built separately by a teammate. The RAG offers it `FCAHandbookResearchPort` |
| Version check and refresh | Not started, see below |

## Decisions

| Decision | Choice |
|---|---|
| What a PR is | A GitHub pull request |
| Scope | SYSC, PRIN, COBS, CONC and the Glossary. CONC because the core agent reviews consumer credit and mostly cites it |
| How the agent uses the RAG | As MCP tools, over HTTP. It never opens Qdrant or the FCA API itself |
| Embeddings | Local model, so any demo PC can run it. It must be the same model for indexing and searching |
| Where the code lives | `04_fca_compliance_agent/handbook_rag`, run as a separate MCP server. Its packages are the `handbook-rag` extra |
| Tools | `search_fca_handbook`, `get_fca_section`, `check_fca_citation` |
| What is an agent | Only the FCA and CCA review of each change, built separately. Triage and the summary stay single LLM steps |
| Consumer Credit Act 1974 | Not in the Handbook. Served by `fca-regulatory-mcp` |
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

## FCA API

Read from the FCA's documentation while signed in. Base URL `https://api.handbook.fca.org.uk`, JSON POST, `Authorization:
Bearer`, 60 requests a minute for each endpoint. Today and future dates only.

| Endpoint | Used for |
|---|---|
| `/get_all_handbook_tree` | Sourcebooks, chapters and sections, with `lastmodifieddate` |
| `/get_latest_handbook_provisions` (`chapter_id`) | The provisions of a chapter, as HTML |
| `/get_latest_glossary` | The Glossary |

- Only use the API. Site terms 35.3 ban scraping; terms 25 and 26 allow reuse under OGL v3.0 with attribution.
- The server stalls on reused connections, so each request opens a new one.
- [ ] Download future versions too (`timeline_date`), and set `effective_to` from them. Today only current rules are loaded.
- [ ] Add other sourcebooks if needed: DISP, SUP and BCOBS are likely for consumer products.

### Credentials

- Put the FCA username and API token in `.env.fca` in the repo root (git-ignored). Never the website password.
- Only the downloader process may read that file. The agent and its tools must not load it, so do not put the token in
  the shared `.env`, which every agent loads.
- If an agent is ever given a shell or file access, run it where `.env` and `.env.fca` are not readable.
- Before the repo is shared, scan the commits for secrets.

## Retrieval quality on real text

18 queries with expected sections fixed beforehand: top 1 for 9, top 3 for 15, top 8 for 16. About 0.1 s a search. A small
set, written by the developer, so a first indication only. Misses: "making cancellation harder than signing up, sludge
practices" (PRIN 2A) and "price cap on payday loans" (CONC 5A.2; it found CONC 5A.4 and 5A.5).

- [ ] 50 or more queries, written by someone else.
- [ ] Turn developer wording into regulatory wording before searching, which is where it is weakest.
- [ ] A long section returns the group under one sub-heading, so exemptions at its start (such as CONC 5.2A.2R) are only in
  the outline. Try filling the rest of the limit with the "Application" group.

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

1. Read `snapshot_date` from any search result. It still needs the time of the last successful check.
2. Fresh (under 48 hours): continue. Stale: continue with a warning. Over 7 days, or no index: stop for a person.
   These limits are suggestions, for the team to agree.
3. Record the snapshot date and `as_of` date in the assessment, so it can be reproduced.
4. Use the planned release date as `as_of`, and also report cited rules that change within 90 days.

Open points: the tree's `lastmodifieddate` may show which chapters changed (to be checked); does the server
version of Qdrant become necessary (local mode allows one process, so a refresh cannot run while an agent is open).

## Next: with the agent's author

- [ ] Agree that the agent uses `search`, `get_section` and `check_fca_citation`, and that code checks every quote afterwards.
- [ ] 10 to 20 labelled PRs, with separate scores for retrieval and verdicts.
- [ ] Give triage the whole change: it sees only the first 1,500 characters, and can drop a change.

Most Handbook rules bind the firm, not a line of code, so output is evidence for a reviewer, not legal advice.
