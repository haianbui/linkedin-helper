from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    serp_api_key: str = ""
    apollo_api_key: str = ""
    proxycurl_api_key: str = ""
    apify_api_key: str = ""

    claude_model: str = "claude-sonnet-4-20250514"
    max_search_results: int = 100
    cache_db_path: str = "data/cache.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context) -> None:
        # Strip whitespace/newlines from API keys (Vercel env vars can have trailing newlines)
        for field in ("anthropic_api_key", "serp_api_key", "apollo_api_key", "proxycurl_api_key", "apify_api_key"):
            val = getattr(self, field)
            if val:
                object.__setattr__(self, field, val.strip())


settings = Settings()
