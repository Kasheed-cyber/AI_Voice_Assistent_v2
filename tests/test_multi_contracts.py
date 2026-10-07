"""Проверки поддержки нескольких договоров в одном разговоре."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from app import storage


def test_merge_keeps_two_contracts_and_tasks():
    call_id = "multi-contract-demo"
    storage.delete_protocol(call_id)
    storage.create_protocol(call_id)

    storage.merge_protocol(call_id, {
        "topic": "Поставка",
        "participants": [],
        "agreements": ["По договору №15 отправить КП до пятницы"],
        "tasks": [{
            "owner": "Менеджер", "task": "Отправить КП", "deadline": "пятница",
            "contract_id": "№15"
        }],
        "key_points": [],
        "contracts": [{
            "contract_id": "№15",
            "contract_name": "Принтеры",
            "agreements": ["Отправить КП до пятницы"],
            "tasks": [{
                "owner": "Менеджер", "task": "Отправить КП", "deadline": "пятница",
                "contract_id": "№15"
            }]
        }]
    })

    storage.merge_protocol(call_id, {
        "topic": "Поставка",
        "participants": [],
        "agreements": ["По договору №18 подтвердить количество"],
        "tasks": [{
            "owner": "Клиент", "task": "Подтвердить количество", "deadline": "20 ноября",
            "contract_id": "№18"
        }],
        "key_points": [],
        "contracts": [{
            "contract_id": "№18",
            "contract_name": "Мониторы",
            "agreements": ["Подтвердить количество"],
            "tasks": [{
                "owner": "Клиент", "task": "Подтвердить количество", "deadline": "20 ноября",
                "contract_id": "№18"
            }]
        }]
    })

    result = storage.get_protocol(call_id)
    assert len(result.contracts) == 2
    assert {c.contract_id for c in result.contracts} == {"№15", "№18"}
    assert len(result.tasks) == 2

    storage.delete_protocol(call_id)


if __name__ == "__main__":
    test_merge_keeps_two_contracts_and_tasks()
    print("OK")
