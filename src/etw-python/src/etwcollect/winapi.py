"""winapi.py - the ctypes declarations for the Windows ETW / TDH / process APIs we call. Windows only.

ctypes lets Python call C functions in Windows DLLs directly. A C struct is declared as a ctypes.Structure
whose _fields_ list the members IN ORDER with their C types; ctypes then lays them out in memory exactly like
the C compiler would (same sizes, same alignment padding), so a pointer to it can be passed to Windows.
Byte sizes are given in the comments ([n B]) because the layout must match the C headers exactly
(evntrace.h, evntcons.h, tdh.h, tlhelp32.h).

Java analogy: like JNA's Structure classes. Nothing here runs any logic; session.py / schema.py /
procnames.py use these declarations.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

ULONG, USHORT, UCHAR = ctypes.c_ulong, ctypes.c_ushort, ctypes.c_ubyte
ULONG64 = LONGLONG = ctypes.c_uint64
TRACEHANDLE = ctypes.c_uint64

# ---------------------------------------------------------------- constants (evntrace.h / evntcons.h)
WNODE_FLAG_TRACED_GUID = 0x00020000
EVENT_TRACE_REAL_TIME_MODE = 0x00000100
EVENT_TRACE_CONTROL_QUERY, EVENT_TRACE_CONTROL_STOP, EVENT_TRACE_CONTROL_FLUSH = 0, 1, 3
EVENT_CONTROL_CODE_ENABLE_PROVIDER = 1
TRACE_LEVEL_VERBOSE = 5
PROCESS_TRACE_MODE_REAL_TIME = 0x00000100
PROCESS_TRACE_MODE_EVENT_RECORD = 0x10000000
INVALID_PROCESSTRACE_HANDLE = 0xFFFFFFFFFFFFFFFF
EVENT_HEADER_FLAG_32_BIT_HEADER = 0x0020
EVENT_HEADER_FLAG_64_BIT_HEADER = 0x0040

ERROR_SUCCESS = 0
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_ALREADY_EXISTS = 183
ERROR_CANCELLED = 1223
ERROR_WMI_INSTANCE_NOT_FOUND = 4201

# tdh.h PROPERTY_FLAGS
PropertyStruct, PropertyParamLength, PropertyParamCount = 0x1, 0x2, 0x4
PropertyParamFixedLength, PropertyParamFixedCount = 0x10, 0x20


class GUID(ctypes.Structure):                       # [16 B]
    _fields_ = [("Data1", ULONG), ("Data2", USHORT), ("Data3", USHORT), ("Data4", UCHAR * 8)]

    @classmethod
    def from_uuid(cls, u) -> "GUID":
        return cls.from_buffer_copy(u.bytes_le)


class WNODE_HEADER(ctypes.Structure):               # [48 B]
    _fields_ = [("BufferSize", ULONG), ("ProviderId", ULONG), ("HistoricalContext", ULONG64),
                ("TimeStamp", LONGLONG), ("Guid", GUID), ("ClientContext", ULONG), ("Flags", ULONG)]


class EVENT_TRACE_PROPERTIES(ctypes.Structure):     # [120 B], the session name follows it in the same buffer
    _fields_ = [("Wnode", WNODE_HEADER), ("BufferSize", ULONG), ("MinimumBuffers", ULONG),
                ("MaximumBuffers", ULONG), ("MaximumFileSize", ULONG), ("LogFileMode", ULONG),
                ("FlushTimer", ULONG), ("EnableFlags", ULONG), ("AgeLimit", ctypes.c_long),
                ("NumberOfBuffers", ULONG), ("FreeBuffers", ULONG), ("EventsLost", ULONG),
                ("BuffersWritten", ULONG), ("LogBuffersLost", ULONG), ("RealTimeBuffersLost", ULONG),
                ("LoggerThreadId", wintypes.HANDLE), ("LogFileNameOffset", ULONG), ("LoggerNameOffset", ULONG)]


class EVENT_DESCRIPTOR(ctypes.Structure):           # [16 B]
    _fields_ = [("Id", USHORT), ("Version", UCHAR), ("Channel", UCHAR), ("Level", UCHAR),
                ("Opcode", UCHAR), ("Task", USHORT), ("Keyword", ULONG64)]


class EVENT_HEADER(ctypes.Structure):               # [80 B]
    _fields_ = [("Size", USHORT), ("HeaderType", USHORT), ("Flags", USHORT), ("EventProperty", USHORT),
                ("ThreadId", ULONG), ("ProcessId", ULONG), ("TimeStamp", LONGLONG), ("ProviderId", GUID),
                ("EventDescriptor", EVENT_DESCRIPTOR), ("ProcessorTime", ULONG64), ("ActivityId", GUID)]


class ETW_BUFFER_CONTEXT(ctypes.Structure):         # [4 B]
    _fields_ = [("ProcessorNumber", UCHAR), ("Alignment", UCHAR), ("LoggerId", USHORT)]


class EVENT_RECORD(ctypes.Structure):               # [112 B] what the callback receives (a pointer to it)
    _fields_ = [("EventHeader", EVENT_HEADER), ("BufferContext", ETW_BUFFER_CONTEXT),
                ("ExtendedDataCount", USHORT), ("UserDataLength", USHORT), ("ExtendedData", ctypes.c_void_p),
                ("UserData", ctypes.c_void_p), ("UserContext", ctypes.c_void_p)]


EVENT_RECORD_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.POINTER(EVENT_RECORD))


class EVENT_TRACE_HEADER(ctypes.Structure):         # [48 B] (legacy, only sized here: part of EVENT_TRACE)
    _fields_ = [("Size", USHORT), ("FieldTypeFlags", USHORT), ("Version", ULONG), ("ThreadId", ULONG),
                ("ProcessId", ULONG), ("TimeStamp", LONGLONG), ("Guid", GUID), ("ProcessorTime", ULONG64)]


class EVENT_TRACE(ctypes.Structure):
    _fields_ = [("Header", EVENT_TRACE_HEADER), ("InstanceId", ULONG), ("ParentInstanceId", ULONG),
                ("ParentGuid", GUID), ("MofData", ctypes.c_void_p), ("MofLength", ULONG),
                ("ClientContext", ULONG)]


class SYSTEMTIME(ctypes.Structure):                 # [16 B]
    _fields_ = [(n, wintypes.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
                                              "wSecond", "wMilliseconds")]


class TIME_ZONE_INFORMATION(ctypes.Structure):      # [172 B]
    _fields_ = [("Bias", ctypes.c_long), ("StandardName", wintypes.WCHAR * 32), ("StandardDate", SYSTEMTIME),
                ("StandardBias", ctypes.c_long), ("DaylightName", wintypes.WCHAR * 32),
                ("DaylightDate", SYSTEMTIME), ("DaylightBias", ctypes.c_long)]


class TRACE_LOGFILE_HEADER(ctypes.Structure):
    _fields_ = [("BufferSize", ULONG), ("Version", ULONG), ("ProviderVersion", ULONG),
                ("NumberOfProcessors", ULONG), ("EndTime", LONGLONG), ("TimerResolution", ULONG),
                ("MaximumFileSize", ULONG), ("LogFileMode", ULONG), ("BuffersWritten", ULONG),
                ("LogInstanceGuid", GUID), ("LoggerName", ctypes.c_void_p), ("LogFileName", ctypes.c_void_p),
                ("TimeZone", TIME_ZONE_INFORMATION), ("BootTime", LONGLONG), ("PerfFreq", LONGLONG),
                ("StartTime", LONGLONG), ("ReservedFlags", ULONG), ("BuffersLost", ULONG)]


class EVENT_TRACE_LOGFILEW(ctypes.Structure):       # what OpenTraceW gets: which session, which callback
    _fields_ = [("LogFileName", wintypes.LPWSTR), ("LoggerName", wintypes.LPWSTR),
                ("CurrentTime", LONGLONG), ("BuffersRead", ULONG), ("ProcessTraceMode", ULONG),
                ("CurrentEvent", EVENT_TRACE), ("LogfileHeader", TRACE_LOGFILE_HEADER),
                ("BufferCallback", ctypes.c_void_p), ("BufferSize", ULONG), ("Filled", ULONG),
                ("EventsLost", ULONG), ("EventRecordCallback", EVENT_RECORD_CALLBACK),
                ("IsKernelTrace", ULONG), ("Context", ctypes.c_void_p)]


# ---------------------------------------------------------------- TDH (tdh.h): the event's schema
class TRACE_EVENT_INFO(ctypes.Structure):           # [112 B] + EVENT_PROPERTY_INFO[PropertyCount] + strings
    _fields_ = [("ProviderGuid", GUID), ("EventGuid", GUID), ("EventDescriptor", EVENT_DESCRIPTOR),
                ("DecodingSource", ctypes.c_int), ("ProviderNameOffset", ULONG), ("LevelNameOffset", ULONG),
                ("ChannelNameOffset", ULONG), ("KeywordsNameOffset", ULONG), ("TaskNameOffset", ULONG),
                ("OpcodeNameOffset", ULONG), ("EventMessageOffset", ULONG), ("ProviderMessageOffset", ULONG),
                ("BinaryXMLOffset", ULONG), ("BinaryXMLSize", ULONG), ("EventNameOffset", ULONG),
                ("EventAttributesOffset", ULONG), ("PropertyCount", ULONG), ("TopLevelPropertyCount", ULONG),
                ("Flags", ULONG)]


class EVENT_PROPERTY_INFO(ctypes.Structure):        # [24 B] one field of the payload
    _fields_ = [("Flags", ctypes.c_int), ("NameOffset", ULONG),
                ("InType", USHORT), ("OutType", USHORT), ("MapNameOffset", ULONG),   # (union: non-struct view)
                ("Count", USHORT), ("Length", USHORT), ("Reserved", ULONG)]


class PROPERTY_DATA_DESCRIPTOR(ctypes.Structure):   # [16 B] "give me property <name>" for TdhGetProperty
    _fields_ = [("PropertyName", ULONG64), ("ArrayIndex", ULONG), ("Reserved", ULONG)]


# ---------------------------------------------------------------- processes (tlhelp32.h)
TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


# ---------------------------------------------------------------- function prototypes
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
tdh = ctypes.WinDLL("tdh", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PPROPS = ctypes.POINTER(EVENT_TRACE_PROPERTIES)

StartTraceW = advapi32.StartTraceW
StartTraceW.argtypes = [ctypes.POINTER(TRACEHANDLE), wintypes.LPCWSTR, PPROPS]
StartTraceW.restype = ULONG

ControlTraceW = advapi32.ControlTraceW
ControlTraceW.argtypes = [TRACEHANDLE, wintypes.LPCWSTR, PPROPS, ULONG]
ControlTraceW.restype = ULONG

EnableTraceEx2 = advapi32.EnableTraceEx2
EnableTraceEx2.argtypes = [TRACEHANDLE, ctypes.POINTER(GUID), ULONG, UCHAR, ULONG64, ULONG64, ULONG, ctypes.c_void_p]
EnableTraceEx2.restype = ULONG

OpenTraceW = advapi32.OpenTraceW
OpenTraceW.argtypes = [ctypes.POINTER(EVENT_TRACE_LOGFILEW)]
OpenTraceW.restype = TRACEHANDLE

ProcessTrace = advapi32.ProcessTrace
ProcessTrace.argtypes = [ctypes.POINTER(TRACEHANDLE), ULONG, ctypes.c_void_p, ctypes.c_void_p]
ProcessTrace.restype = ULONG

CloseTrace = advapi32.CloseTrace
CloseTrace.argtypes = [TRACEHANDLE]
CloseTrace.restype = ULONG

TdhGetEventInformation = tdh.TdhGetEventInformation
TdhGetEventInformation.argtypes = [ctypes.POINTER(EVENT_RECORD), ULONG, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(ULONG)]
TdhGetEventInformation.restype = ULONG

TdhGetPropertySize = tdh.TdhGetPropertySize
TdhGetPropertySize.argtypes = [ctypes.POINTER(EVENT_RECORD), ULONG, ctypes.c_void_p, ULONG,
                               ctypes.POINTER(PROPERTY_DATA_DESCRIPTOR), ctypes.POINTER(ULONG)]
TdhGetPropertySize.restype = ULONG

TdhGetProperty = tdh.TdhGetProperty
TdhGetProperty.argtypes = [ctypes.POINTER(EVENT_RECORD), ULONG, ctypes.c_void_p, ULONG,
                           ctypes.POINTER(PROPERTY_DATA_DESCRIPTOR), ULONG, ctypes.c_void_p]
TdhGetProperty.restype = ULONG

CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
CreateToolhelp32Snapshot.restype = wintypes.HANDLE
Process32FirstW = kernel32.Process32FirstW
Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32FirstW.restype = wintypes.BOOL
Process32NextW = kernel32.Process32NextW
Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32NextW.restype = wintypes.BOOL
OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE
QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
QueryFullProcessImageNameW.restype = wintypes.BOOL
CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL
QueryDosDeviceW = kernel32.QueryDosDeviceW
QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
QueryDosDeviceW.restype = wintypes.DWORD

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def check(code: int, what: str) -> None:
    if code != ERROR_SUCCESS:
        raise OSError(code, f"{what} failed: {ctypes.FormatError(code).strip()} ({code})")
