import asyncio
from src.core.agentic_router import AgenticRouter
from dotenv import load_dotenv

async def test_routing():
    load_dotenv()
    router = AgenticRouter()
    
    test_instructions = [
        "Audit website for lead 123",
        "How is our database doing? Give me a status check.",
        "How many leads are in Miami?",
        "Find new pizzerias in Miami",
        "Generate an outreach email for lead 456",
        "Generate a LinkedIn invite for lead 789"
    ]
    
    print("--- Testing Modern Agentic Router (Function Calling) ---")
    for instruction in test_instructions:
        print(f"\nInstruction: {instruction}")
        plan = await router.route_instruction(instruction)
        print(f"Routed Task: {plan.get('task')}")
        print(f"Params: {plan.get('params')}")
        print(f"Reasoning: {plan.get('reasoning')}")

if __name__ == "__main__":
    asyncio.run(test_routing())
