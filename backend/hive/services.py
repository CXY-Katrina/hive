from types import SimpleNamespace
from .db import Database
from .identity import Identity
from .inventory import Inventory
from .metrics import MetricCatalog
from .telemetry import Telemetry
from .resources import ResourceService
from .reporting import Reporting


def build_services(settings):
    from .ssh import SSHTransport
    from .hardware import AscendAdapter
    from .probes import AdmissionProbe
    from .execution import Execution
    from .cleanup import Cleanup
    from .compute_benchmark import ComputeBenchmark
    db=Database(settings)
    inventory=Inventory(db,settings)
    transport=SSHTransport(settings)
    telemetry_transport=SSHTransport(settings)
    benchmark_transport=SSHTransport(settings)
    adapters={"ascend":AscendAdapter(transport)}
    telemetry_adapters={"ascend":AscendAdapter(telemetry_transport)}
    inventory.supported_adapters=set(adapters)
    catalog=MetricCatalog()
    telemetry=Telemetry(db,inventory,telemetry_adapters,catalog,settings)
    resources=ResourceService(db,settings)
    return SimpleNamespace(db=db,settings=settings,identity=Identity(db,settings),inventory=inventory,
        transport=transport,telemetry_transport=telemetry_transport,benchmark_transport=benchmark_transport,
        compute_benchmark=ComputeBenchmark(db,settings,inventory,telemetry,benchmark_transport),
        adapters=adapters,catalog=catalog,telemetry=telemetry,resources=resources,reporting=Reporting(db,settings),
        probes=AdmissionProbe(db,transport,adapters,inventory),
        execution=Execution(db,transport,inventory,resources,adapters,settings),
        cleanup=Cleanup(db,transport,inventory,telemetry,resources))
