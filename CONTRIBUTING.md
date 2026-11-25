# How we work in this repo

Everyone works in this one repo, each in their own folder, so we don't step on each other's toes.

| Folder | Owner |
|--------|-------|
| `00_control_tower/` | Lukman |
| `01_kyc_agent/` | Nam |
| `02_transaction_monitoring_agent/` | Marcus |
| `03_document_summary_agent/` | Alejandro |
| `docs/process_maps/` | everyone (your own page) |

## First time

1. Accept your LangSmith invite: sign in with your registered email, **not SSO**. Create an API key under
   Settings → API keys (it is shown once) and note your workspace ID.
2. Install git, [uv](https://docs.astral.sh/uv/) and VS Code (with the Python and Jupyter extensions). On Windows, use
   PowerShell or WSL, and install Visual Studio C++ Build Tools and the Visual Studio 2015 C++ runtime.
3. Clone and install:

```bash
git clone https://github.com/milltechfx/LangchainHackathon.git
cd LangchainHackathon
uv sync --all-packages        # installs everything into .venv (Python 3.13 is pinned in .python-version)
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
```

4. Fill in `.env`: `ATTENDEE_NAME`, `LANGSMITH_API_KEY` and `LANGSMITH_WORKSPACE_ID`.
5. Check it works: open `01_kyc_agent/modules/01_kyc_agent.ipynb`, pick `.venv` as the kernel, run the first cells, and
   confirm a trace appears in LangSmith.

If `uv sync` fails building a package, run `uv sync --all-packages --python 3.13`. Corporate laptops that block
installs: use a personal laptop (the data is synthetic). The network needs github.com, smith.langchain.com,
api.smith.langchain.com, gateway.smith.langchain.com, pypi.org and astral.sh.

## Every piece of work

```bash
git checkout main && git pull                 # start from the latest main
git checkout -b nam/kyc-ratings               # a branch is your own copy: yourname/what-you-are-doing
# ...make your changes...
git add 01_kyc_agent docs/process_maps/01_kyc.md
git commit -m "KYC: add medium rating and EDD rules"
git push -u origin nam/kyc-ratings
```

Before you open a pull request, check your agent still plugs into the supervisor:

```bash
uv run pytest -q 00_control_tower/tests/test_contracts.py
```

Then open a **pull request** (PR) on GitHub from your branch into `main`, and post the link in the team chat.
Someone else gives it a quick look and merges it.

## Rules

- Never commit `.env` (it holds your API key). It is already in `.gitignore`. Never paste keys into chats.
- Synthetic data only. No MillTech or client data, and no connections to company systems.
- Outputs are drafts for a human reviewer, not legal, compliance or regulatory advice.
- Submission (Lukman): `00_control_tower/control_tower.ipynb` and `00_control_tower/agents.py` must run on `main` by 16:00.
- If you change what your agent takes in or gives back, update the "For the supervisor" box on your process-map
  page in the same PR.
- Stuck? Paste the error into Claude or ping Lukman. LangChain engineers will also be with us on the day.
