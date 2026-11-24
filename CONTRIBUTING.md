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

```bash
git clone <repo link from Lukman>
cd <repo folder>
uv sync --all-packages        # installs everything into .venv
cp .env.example .env          # then fill in your LangSmith key and ATTENDEE_NAME
```

## Every piece of work

```bash
git checkout main && git pull                 # start from the latest main
git checkout -b nam/kyc-ratings               # a branch is your own copy: yourname/what-you-are-doing
# ...make your changes...
git add 01_kyc_agent docs/process_maps/01_kyc.md
git commit -m "KYC: add medium rating and EDD rules"
git push -u origin nam/kyc-ratings
```

Then open a **pull request** (PR) on GitHub from your branch into `main`, and post the link in the team chat.
Someone else gives it a quick look and merges it.

## Rules

- Never commit `.env` (it holds your API key). It is already in `.gitignore`.
- Synthetic data only. No MillTech or client data, and no connections to company systems.
- If you change what your agent takes in or gives back, update the "For the supervisor" box on your process-map
  page in the same PR.
- Stuck? Paste the error into Claude or ping Lukman. LangChain engineers will also be with us on the day.
