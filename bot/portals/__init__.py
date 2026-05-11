from .linkedin import LinkedInPortal
from .indeed import IndeedPortal
from .getonyboard import GetOnBoardPortal
from .laborum import LaborumPortal
from .computrabajo import ComputrabajoPortal
from .chiletrabajos import ChileTrabajosPortal

PORTAL_REGISTRY = {
    "linkedin":       LinkedInPortal,
    "indeed":         IndeedPortal,
    "getonyboard":    GetOnBoardPortal,
    "laborum":        LaborumPortal,
    "computrabajo":   ComputrabajoPortal,
    "chiletrabajos":  ChileTrabajosPortal,
}
