"""FinCrime Control Tower: the agents we submit (LangChain FSI Hackathon, London, 25 Nov 2026).

Team MillTech: Lukman Shoge (supervisor), Yun Nam Chan (KYC), Marcus Georgiou (transaction monitoring),
Alejandro Bondjale (regulatory change). All data is synthetic. Outputs are drafts for a human reviewer, not legal,
compliance or regulatory advice.

Four agents, one shared memory:

| Name                  | Built with                                   | Folder                              |
|-----------------------|----------------------------------------------|-------------------------------------|
| `supervisor`          | Deep Agents (`create_deep_agent`)            | 00_control_tower                    |
| `kyc_agent`           | LangGraph graph with human review            | 01_kyc_agent                        |
| `transaction_agent`   | LangChain `create_agent`, memory, human review| 02_transaction_monitoring_agent     |
| `regulatory_agent`    | Deep agent with parallel section sub-agents  | 03_document_summary_agent           |

    from agents import supervisor
    supervisor.invoke({"messages": [{"role": "user", "content": "PS26-7 has been published for FIRM-001."}]})

The notebook `control_tower.ipynb` walks through the whole story step by step.
"""
from control_tower import memory
from control_tower.pillars import kyc, reg, tm
from control_tower.policy import BASELINE, PS26_7_REFERENCE, PolicyParams
from control_tower.reviews import answer_review
from control_tower.supervisor import agent as supervisor, build_supervisor

kyc_agent = kyc.graph()
transaction_agent = tm.agent_for(memory.get_store())
regulatory_agent = reg.reg_agent()

__all__ = [
    "supervisor", "build_supervisor", "kyc_agent", "transaction_agent", "regulatory_agent",
    "memory", "kyc", "tm", "reg", "answer_review", "PolicyParams", "BASELINE", "PS26_7_REFERENCE",
]
