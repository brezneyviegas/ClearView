"""`python -m app` — start the gateway with .env/env-var config."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("CLEARVIEW_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLEARVIEW_PORT", "8000")),
    )
