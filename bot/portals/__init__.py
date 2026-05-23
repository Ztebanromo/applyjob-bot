from .linkedin import LinkedInPortal
from .indeed import IndeedPortal
from .getonyboard import GetOnBoardPortal
from .laborum import LaborumPortal
from .computrabajo import ComputrabajoPortal
from .chiletrabajos import ChileTrabajosPortal
from .weworkremotely import WeWorkRemotelyPortal
from .remotive import RemotivePortal
from .remoteco import RemoteCoPortal

PORTAL_REGISTRY = {
    # Portales locales (Chile)
    "linkedin":        LinkedInPortal,
    "indeed":          IndeedPortal,
    "getonyboard":     GetOnBoardPortal,
    "laborum":         LaborumPortal,
    "computrabajo":    ComputrabajoPortal,
    "chiletrabajos":   ChileTrabajosPortal,
    # Portales remotos internacionales
    "weworkremotely":  WeWorkRemotelyPortal,
    "remotive":        RemotivePortal,
    "remoteco":        RemoteCoPortal,
}
