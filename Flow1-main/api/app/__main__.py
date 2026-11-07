"""`python -m app` (run from the api/ directory): start Uvicorn with exactly ONE worker (contract D10)."""
import os
import sys

def main() -> None:
    import uvicorn
    try:
        import tradeflow_core  # noqa: F401
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
    from .main import create_app
    from .settings import from_env
    app = create_app(from_env())
    uvicorn.run(app, host=os.environ.get("TRADEFLOW_HOST", "127.0.0.1"), port=int(os.environ.get("TRADEFLOW_PORT", "8000")), workers=1)

if __name__ == "__main__":
    main()
