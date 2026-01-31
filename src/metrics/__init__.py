"""Metrics and observability module for algo-trading.

This module provides comprehensive metrics collection, monitoring, and alerting:

- collector: Metrics collection with async-safe counters, gauges, and histograms
- exporters: Prometheus, StatsD, in-memory, and log exporters
- dashboard: Real-time dashboard data aggregation
- alerts: Threshold-based alerting with multiple channels
- integration: Easy integration with adapters and HTTP clients

Usage:
    from src.metrics import init_metrics, get_collector, get_dashboard
    
    # Initialize all metrics components
    await init_metrics()
    
    # Get collector and record metrics
    collector = get_collector()
    await collector.record_request_started("binance", "GET")
    
    # Get dashboard data
    dashboard = get_dashboard()
    health = await dashboard.get_exchange_health("binance")
"""

from src.metrics.collector import (
    MetricsCollector,
    AsyncCounter,
    AsyncGauge,
    AsyncHistogram,
    RequestTracker,
    OrderTracker,
    HistogramSnapshot,
    MetricType,
    MetricValue,
    init_collector,
    get_collector,
    collector
)

from src.metrics.exporters import (
    BaseExporter,
    PrometheusExporter,
    StatsDExporter,
    InMemoryStore,
    LogExporter,
    CompositeExporter,
    MetricsExportManager,
    init_exporters,
    get_export_manager,
    export_manager
)

from src.metrics.dashboard import (
    DashboardData,
    ExchangeHealth,
    RateLimitStatus,
    TradingSummary,
    OrderMetrics,
    ConnectionMetrics,
    SystemOverview,
    DashboardSnapshot,
    HealthStatus,
    CircuitState,
    init_dashboard,
    get_dashboard,
    dashboard
)

from src.metrics.alerts import (
    AlertManager,
    AlertRule,
    Alert,
    AlertHistory,
    AlertSeverity,
    AlertStatus,
    AlertRuleType,
    AlertChannel,
    LogAlertChannel,
    WebhookAlertChannel,
    EmailAlertChannel,
    PagerDutyAlertChannel,
    create_default_rules,
    init_alert_manager,
    get_alert_manager,
    alert_manager
)


async def init_metrics(
    namespace: str = "algo_trading",
    enable_prometheus: bool = True,
    prometheus_port: int = 9090,
    enable_statsd: bool = False,
    statsd_host: str = "localhost",
    statsd_port: int = 8125,
    enable_logging: bool = True,
    export_interval: float = 10.0,
    enable_alerts: bool = True,
    alert_evaluation_interval: float = 10.0
) -> tuple:
    """Initialize all metrics and observability components.
    
    This is a convenience function to set up the complete metrics stack.
    
    Args:
        namespace: Metric namespace/prefix
        enable_prometheus: Enable Prometheus exporter
        prometheus_port: Prometheus HTTP port
        enable_statsd: Enable StatsD exporter
        statsd_host: StatsD host
        statsd_port: StatsD port
        enable_logging: Enable log exporter
        export_interval: Metrics export interval (seconds)
        enable_alerts: Enable alerting
        alert_evaluation_interval: Alert rule evaluation interval
        
    Returns:
        Tuple of (collector, export_manager, dashboard, alert_manager)
    """
    # Initialize collector
    collector = init_collector(namespace)
    
    # Initialize exporters
    export_mgr = init_exporters(
        collector=collector,
        export_interval=export_interval,
        enable_prometheus=enable_prometheus,
        enable_statsd=enable_statsd,
        enable_in_memory=True,
        enable_logging=enable_logging,
        prometheus_port=prometheus_port,
        statsd_host=statsd_host,
        statsd_port=statsd_port
    )
    await export_mgr.start()
    
    # Initialize dashboard
    dashboard = init_dashboard(collector)
    await dashboard.start()
    
    # Initialize alerts
    alert_mgr = None
    if enable_alerts:
        alert_mgr = init_alert_manager(
            collector,
            dashboard,
            evaluation_interval=alert_evaluation_interval
        )
        await alert_mgr.start()
    
    return collector, export_mgr, dashboard, alert_mgr


async def shutdown_metrics() -> None:
    """Shutdown all metrics and observability components."""
    from src.metrics.alerts import alert_manager
    from src.metrics.dashboard import dashboard
    from src.metrics.exporters import export_manager
    
    if alert_manager:
        await alert_manager.stop()
    
    if dashboard:
        await dashboard.stop()
    
    if export_manager:
        await export_manager.stop()


__all__ = [
    # Collector
    'MetricsCollector',
    'AsyncCounter',
    'AsyncGauge',
    'AsyncHistogram',
    'RequestTracker',
    'OrderTracker',
    'HistogramSnapshot',
    'MetricType',
    'MetricValue',
    'init_collector',
    'get_collector',
    'collector',
    
    # Exporters
    'BaseExporter',
    'PrometheusExporter',
    'StatsDExporter',
    'InMemoryStore',
    'LogExporter',
    'CompositeExporter',
    'MetricsExportManager',
    'init_exporters',
    'get_export_manager',
    'export_manager',
    
    # Dashboard
    'DashboardData',
    'ExchangeHealth',
    'RateLimitStatus',
    'TradingSummary',
    'OrderMetrics',
    'ConnectionMetrics',
    'SystemOverview',
    'DashboardSnapshot',
    'HealthStatus',
    'CircuitState',
    'init_dashboard',
    'get_dashboard',
    'dashboard',
    
    # Alerts
    'AlertManager',
    'AlertRule',
    'Alert',
    'AlertHistory',
    'AlertSeverity',
    'AlertStatus',
    'AlertRuleType',
    'AlertChannel',
    'LogAlertChannel',
    'WebhookAlertChannel',
    'EmailAlertChannel',
    'PagerDutyAlertChannel',
    'create_default_rules',
    'init_alert_manager',
    'get_alert_manager',
    'alert_manager',
    
    # Convenience functions
    'init_metrics',
    'shutdown_metrics'
]
