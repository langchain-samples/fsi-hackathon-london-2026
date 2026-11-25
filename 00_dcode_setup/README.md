# Deep Agents Code

Deep Agents Code is a terminal coding agent built on the Deep Agents SDK. In this hackathon you can use it to help
build and debug the agents in the other folders. Its LLM calls go through the LangSmith LLM Gateway and are traced
to LangSmith.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- A LangSmith API key and workspace ID (ask a workshop host if you don't have them)

## Install

Install Deep Agents Code as a standalone tool, so it is available from any folder:

```bash
uv tool install deepagents-code
```

or

```bash
curl -LsSf https://langch.in/dcode | bash
```

This puts two equivalent commands on your `PATH`: `dcode` and `deepagents-code`. Check the install:

```bash
dcode --help
```

To upgrade later:

```bash
uv tool upgrade deepagents-code
```

If `dcode` is not found, run `uv tool update-shell` and open a new terminal.

## Configure

Deep Agents Code reads its settings from `~/.deepagents/.env`. Create it from the example in this folder:

```bash
mkdir -p ~/.deepagents
cp 00_dcode_setup/.env.example ~/.deepagents/.env
chmod 600 ~/.deepagents/.env
```

Then edit `~/.deepagents/.env` and replace the placeholders:

| Variable | What to put there |
|----------|-------------------|
| `ATTENDEE_NAME` | Your name, so your traces land in your own LangSmith project (`deepagents-code-<name>`) |
| `DEEPAGENTS_CODE_LANGSMITH_API_KEY` | Your LangSmith API key. It is also used for the LLM Gateway |
| `DEEPAGENTS_CODE_LANGSMITH_WORKSPACE_ID` | Your LangSmith workspace ID |

The remaining values (tracing flag, API URL, gateway URL) are already set. Keep your key scoped to what you need,
and never commit a `.env` file or share it. The repo's `.gitignore` already excludes `.env` files.

## Run

Start it from the folder you want to work in, for example a workshop topic:

```bash
cd 01_kyc_agent
dcode
```

Describe what you want in plain language. Deep Agents Code asks for approval before it edits files or runs
commands. Your sessions appear as traces in the `deepagents-code-<your name>` project in LangSmith.

## Troubleshooting

- **`dcode: command not found`**: run `uv tool update-shell`, then restart your terminal.
- **Authentication errors**: check the API key and workspace ID in `~/.deepagents/.env`, and that none of the
  `<replace-with-...>` placeholders remain.
- **No traces in LangSmith**: confirm `LANGSMITH_TRACING="true"` and that `ATTENDEE_NAME` is set.
