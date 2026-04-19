"""
Usage examples for the tracing module.
"""

import asyncio
from tracing import (
    AsyncContextScope,
    ContextManager,
    ContextScope,
    get_logger,
    extract_context,
    inject_context,
    traced,
)


# Setup logger
logger = get_logger("examples")


def example_basic_context():
    """Basic context creation and usage."""
    print("\n=== Basic Context Example ===")

    # Create a root context
    ctx = ContextManager.create_root(metadata={"service": "payment-api"})

    print(f"Request ID: {ctx.request_id}")
    print(f"Trace ID: {ctx.trace_id}")
    print(f"Span ID: {ctx.span_id}")

    # Log with automatic context injection
    logger.info("Payment processing started")

    # Create child context for sub-operation
    db_ctx = ctx.child("database_query")
    print(f"\nChild Span ID: {db_ctx.span_id}")
    print(f"Parent Span ID: {db_ctx.parent_span_id}")

    logger.info("Database query executed", extra={"query_time_ms": 45})


def example_context_scope():
    """Using context scopes for automatic cleanup."""
    print("\n=== Context Scope Example ===")

    with ContextScope(metadata={"endpoint": "/api/orders"}) as ctx:
        # Context is automatically set
        print(f"Current context: {ContextManager.get_current().request_id}")

        logger.info("Request received")

        # Nested scope
        with ContextScope(span_name="validate_order"):
            logger.info("Validating order")

        # Back to parent context
        logger.info("Order validated")

    # Context automatically cleared
    print(f"Context after scope: {ContextManager.get_current()}")


async def example_async_context():
    """Async context propagation."""
    print("\n=== Async Context Example ===")

    async with AsyncContextScope(metadata={"service": "async-worker"}) as ctx:
        logger.info("Async job started")

        # Simulate async work
        await asyncio.sleep(0.1)

        async with AsyncContextScope(span_name="fetch_data"):
            logger.info("Fetching data...")
            await asyncio.sleep(0.1)

        logger.info("Async job completed")


def example_http_propagation():
    """HTTP header propagation for distributed tracing."""
    print("\n=== HTTP Propagation Example ===")

    # Simulate incoming request with W3C headers
    incoming_headers = {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "X-Request-ID": "user-request-123",
    }

    # Extract context from incoming request
    ctx = extract_context(incoming_headers)
    ContextManager.set_current(ctx)

    print(f"Extracted trace: {ctx.trace_id}")
    print(f"Request ID: {ctx.request_id}")

    # Do some work
    logger.info("Processing request")

    # Prepare outgoing request to another service
    child_ctx = ctx.child("call_inventory_service")
    outgoing_headers = inject_context(child_ctx)

    print(f"\nOutgoing headers:")
    for key, value in outgoing_headers.items():
        print(f"  {key}: {value}")


@traced(span_name="process_order", log_args=True)
def process_order(order_id: str, amount: float):
    """Example function with tracing decorator."""
    logger.info(f"Processing order {order_id}")
    # Simulate work
    return {"order_id": order_id, "status": "processed"}


def example_traced_decorator():
    """Using the traced decorator."""
    print("\n=== Traced Decorator Example ===")

    # Create context first
    ContextManager.create_root()

    result = process_order("ORD-123", 99.99)
    print(f"Result: {result}")


async def example_http_client():
    """Using the traced HTTP client."""
    print("\n=== HTTP Client Example ===")

    # Import HTTP client
    from tracing.http import TracedHTTPClient

    async with TracedHTTPClient() as client:
        # Every request gets automatic tracing
        try:
            response = await client.get("https://httpbin.org/get")
            print(f"Status: {response['status']}")
        except Exception as e:
            print(f"Request failed: {e}")


def example_structured_logging():
    """Structured JSON logging example."""
    print("\n=== Structured Logging Example ===")

    from tracing import setup_logging
    import logging

    # Setup JSON logging
    setup_logging(level=logging.INFO, json_format=True)

    # Create context
    ctx = ContextManager.create_root(metadata={"user_id": "12345"})

    # All logs will be JSON with context
    logger = get_logger("structured")
    logger.info("User action", extra={"action": "login", "ip": "192.168.1.1"})


def main():
    """Run all examples."""
    print("Tracing Module Examples")
    print("=" * 50)

    example_basic_context()
    example_context_scope()

    # Run async examples
    asyncio.run(example_async_context())

    example_http_propagation()
    example_traced_decorator()

    # Uncomment to test HTTP client (requires internet)
    # asyncio.run(example_http_client())

    # Uncomment to see JSON logging
    # example_structured_logging()

    print("\n" + "=" * 50)
    print("Examples completed!")


if __name__ == "__main__":
    main()
