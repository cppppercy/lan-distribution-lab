"""Local-only development server entry point."""

import uvicorn


def main() -> None:
    """Run the service with the T001 safe default listener."""
    uvicorn.run(
        "lan_distribution_lab.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
