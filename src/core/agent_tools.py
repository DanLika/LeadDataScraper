def get_agent_tools():
    from google.genai import types
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="seo_audit",
                    description="Audit one or many websites for SEO issues.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "unique_key": {"type": "STRING", "description": "The unique key of a specific lead to audit."}
                        }
                    }
                ),
                types.FunctionDeclaration(
                    name="status_check",
                    description="Get a summary of database health and lead counts.",
                ),
                types.FunctionDeclaration(
                    name="database_query",
                    description="Query the lead database using natural language.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "query_text": {"type": "STRING", "description": "The natural language query to run against the database."}
                        },
                        "required": ["query_text"]
                    }
                ),
                types.FunctionDeclaration(
                    name="outreach_draft",
                    description="Generate a personalized email draft for a specific lead.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "unique_key": {"type": "STRING", "description": "The unique key of the lead."}
                        },
                        "required": ["unique_key"]
                    }
                ),
                types.FunctionDeclaration(
                    name="linkedin_draft",
                    description="Generate a personalized LinkedIn invitation for a specific lead.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "unique_key": {"type": "STRING", "description": "The unique key of the lead."}
                        },
                        "required": ["unique_key"]
                    }
                ),
                types.FunctionDeclaration(
                    name="get_insights",
                    description="Get strategic analysis and insights from the lead database.",
                ),
                types.FunctionDeclaration(
                    name="discovery_search",
                    description="Find new leads on Google Maps.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "query": {"type": "STRING", "description": "Search query (e.g. 'pizzeria')."},
                            "location": {"type": "STRING", "description": "Geographic location (e.g. 'Miami')."}
                        },
                        "required": ["query"]
                    }
                ),
                types.FunctionDeclaration(
                    name="run_massive_pipeline",
                    description="Trigger a full enrichment and audit pipeline for multiple leads.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "filters": {"type": "STRING", "description": "Optional filters to select leads (e.g. 'high-risk')."}
                        }
                    }
                ),
                types.FunctionDeclaration(
                    name="deep_hunt",
                    description="Proactively find social media links and deep contact data for a lead.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "unique_key": {"type": "STRING", "description": "The unique key of the lead."}
                        },
                        "required": ["unique_key"]
                    }
                ),
                types.FunctionDeclaration(
                    name="campaign_strategy",
                    description="Generate a bulk outreach campaign strategy for a segment of leads.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "filters": {"type": "STRING", "description": "Optional filters to select leads."}
                        }
                    }
                )
            ]
        )
    ]
