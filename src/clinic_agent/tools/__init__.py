from clinic_agent.tools.availability import check_availability
from clinic_agent.tools.booking import book_appointment
from clinic_agent.tools.call_control import end_call
from clinic_agent.tools.discovery import list_appointment_types, list_branches, list_doctors
from clinic_agent.tools.identify import identify_or_create_patient, set_branch
from clinic_agent.tools.manage import cancel_appointment, find_upcoming_appointments, reschedule_appointment

ALL_TOOLS = [
    list_branches,
    set_branch,
    list_doctors,
    list_appointment_types,
    identify_or_create_patient,
    check_availability,
    book_appointment,
    find_upcoming_appointments,
    reschedule_appointment,
    cancel_appointment,
    end_call,
]

__all__ = ["ALL_TOOLS"]
