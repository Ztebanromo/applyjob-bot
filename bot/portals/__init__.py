from .linkedin import LinkedInPortal
from .indeed import IndeedPortal
from .getonyboard import GetOnBoardPortal
from .laborum import LaborumPortal
from .computrabajo import ComputrabajoPortal
from .chiletrabajos import ChileTrabajosPortal
from .weworkremotely import WeWorkRemotelyPortal
from .remotive import RemotivePortal
from .remoteco import RemoteCoPortal
from .trabajando import TrabajandoPortal
from .infojobs_cl import InfoJobsCLPortal

PORTAL_REGISTRY = {
    # Portales locales (Chile)
    "linkedin":        LinkedInPortal,
    "indeed":          IndeedPortal,
    "getonyboard":     GetOnBoardPortal,
    "laborum":         LaborumPortal,
    "computrabajo":    ComputrabajoPortal,
    "chiletrabajos":   ChileTrabajosPortal,
    "trabajando":      TrabajandoPortal,
    "infojobs":        InfoJobsCLPortal,
    # Portales remotos internacionales
    "weworkremotely":  WeWorkRemotelyPortal,
    "remotive":        RemotivePortal,
    "remoteco":        RemoteCoPortal,
}
