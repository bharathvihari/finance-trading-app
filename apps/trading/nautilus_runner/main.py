from datetime import datetime, timezone


def main() -> None:
    # Replace this stub with NautilusTrader engine bootstrap, venues, and strategy loading.
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] trading service placeholder is running")
    print("Next: wire IBKR adapter config, strategy registry, and event bus publishing.")


if __name__ == "__main__":
    main()
