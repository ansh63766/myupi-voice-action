import asyncio
from db.engine import get_async_session
from orchestrator import Orchestrator
from agents.types import PipelineState, IntentOutput, ExtractedSlots, EntityResolutionResult
from registry.loader import ActionEntry
import logging
logging.basicConfig(level=logging.INFO)

async def main():
    async for db in get_async_session():
        orch = Orchestrator()
        
        state = PipelineState(session_id="test-123", user_id="user-001")
        state.intent = IntentOutput(
            intent_label="raise_chargeback",
            extracted_slots=ExtractedSlots(txn_id="__ALL__"),
            language="en",
            confidence=0.9
        )
        state.action_entry = ActionEntry(
            action_id="chargeback",
            required_slots=["txn_id"],
            disambiguation_source="user_eligible_transactions",
            risk_tier=2
        )
        state.entity_resolution = EntityResolutionResult()
        state.entity_resolution.needs_disambiguation.append("txn_id")
        
        state.raw_input = "Show me all active mandates"
        
        new_state = await orch.process(state, db, selected_entity=None)
        
        print("\n--- AFTER PROCESS ---")
        print("Intent extracted slots:", new_state.intent.extracted_slots.model_dump())
        if new_state.entity_resolution:
            print("Needs disambiguation:", new_state.entity_resolution.needs_disambiguation)
            print("Unresolvable:", new_state.entity_resolution.unresolvable)
            print("Disambiguation options keys:", list(new_state.entity_resolution.disambiguation_options.keys()))
        else:
            print("Entity resolution is NONE")
            
        break

if __name__ == "__main__":
    asyncio.run(main())
