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
    from .onboarding import Onboarding
    from .workflows import Workflows
    from .sources import SourceService
    from .presets import Presets
    from .preset_archive import DEFAULT_ROOT
    from .node_mappings import NodeMappings
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
    sources=SourceService()
    node_mappings=NodeMappings(db)
    workflows=Workflows(db,resources,inventory,transport,settings,sources)
    workflows.node_mappings=node_mappings
    return SimpleNamespace(db=db,settings=settings,identity=Identity(db,settings),inventory=inventory,
        transport=transport,telemetry_transport=telemetry_transport,benchmark_transport=benchmark_transport,
        onboarding=Onboarding(settings),
        sources=sources,
        node_mappings=node_mappings,
        presets=Presets(db,sources,settings.workflow_sample_preset_id,archive_root=DEFAULT_ROOT),
        workflows=workflows,
        compute_benchmark=ComputeBenchmark(db,settings,inventory,telemetry,benchmark_transport),
        adapters=adapters,catalog=catalog,telemetry=telemetry,resources=resources,reporting=Reporting(db,settings),
        probes=AdmissionProbe(db,transport,adapters,inventory),
        execution=Execution(db,transport,inventory,resources,adapters,settings),
        cleanup=Cleanup(db,transport,inventory,telemetry,resources))
