namespace cpp Timeline

enum SourceType {
  DataLog = 1,
  InterruptLog = 2,
  EventLog = 3,
  PowerLog = 4,
  CallStack = 5,
}

enum DataType {
  Unknown = 1,
  Unsigned8 = 2,
  Unsigned16 = 3,
  Unsigned32 = 4,
  Signed8 = 5,
  Signed16 = 6,
  Signed32 = 7,
  Float16 = 8
  Float32 = 9,
}

struct DataLogSource {
  1: string id,
  2: string symbol,
  3: DataType type,
}

struct InterruptLogSource {
  1: string id,
  2: string name,
}

struct EventLogSource {
  1: string id,
  2: string name,
}

struct PowerLogSource {
  1: string id,
  2: string unit,
  3: string name,
}

struct CallStackSource { }

union Source {
  1: DataLogSource dataLogSource;
  2: InterruptLogSource interruptLogSource;
  3: EventLogSource eventLogSource;
  4: PowerLogSource powerLogSource;
  5: CallStackSource callStackSource;
}

// -------------------------
// Debugger -> Client messages
// -------------------------

struct SourceAddedNotification {
  1: Source source,
}

struct SourceRemovedNotification {
  1: Source source,
}

struct DataAvailableNotification {
  1: Source source,
  2: i64 startTimeNs,
  3: i64 endTimeNs,
}

struct DataLogPoint {
  1: i64 timeNs,
  2: i64 value,
}

struct DataLogResponse {
  1: DataLogSource source,
  2: list<DataLogPoint> data,
}

enum InterruptLogState {
  Enter = 1,
  Exit = 2,
}

struct InterruptLogPoint {
  1: i64 timeNs,
  2: InterruptLogState state,
}

struct InterruptLogResponse {
  1: InterruptLogSource source,
  2: list<InterruptLogPoint> data,
}

struct EventLogPoint {
  1: i64 timeNs,
  2: i64 value,
}

struct EventLogResponse {
  1: EventLogSource source,
  2: list<EventLogPoint> data,
}

enum CallStackNodeState {
  Complete = 1,
  StartUnknown = 2,
  EndUnknown = 3,
  StartEndUnknown = 4,
}

struct CallStackNode {
  1: string name,
  2: i64 startTimeNs,
  3: i64 endTimeNs,
  4: CallStackNodeState state,
  5: list<CallStackNode> children,
}

struct CallStackResponse {
  1: CallStackSource source,
  2: list<CallStackNode> data,
}

struct ErrorResponse {
  1: string message,
}

// Response to a client's initialize request, describing the current state of
// available data sources and which source types are enabled for collection.
struct InitializeResponse {
  // The active data sources (specific data logs, interrupts, etc.) that the
  // debugger is currently tracking.
  1: list<Source> sources,
  // The source types for which low-level data fetching is enabled in the
  // debugger. Sources whose type is not in this list will not have data
  // collected. Use EnableSourceTypeNotification to enable a source type.
  2: list<SourceType> enabledSourceTypes,
  // The source types which are supported by the debugger driver.
  3: list<SourceType> supportedSourceTypes,
}

struct PowerLogPoint {
  1: i64 timeNs,
  2: double value,
}

struct PowerLogResponse {
  1: PowerLogSource source,
  2: list<PowerLogPoint> data,
}

enum DebuggerEventNotification {
  Started = 1,
  Stopped = 2,
  Reset = 3,
}

union DebuggerToClientMessage {
  1: ErrorResponse errorResponse,
  2: InitializeResponse initializeResponse,
  3: SourceAddedNotification sourceAddedNotification,
  4: SourceRemovedNotification sourceRemovedNotification,
  5: DataAvailableNotification dataAvailableNotification,
  6: DataLogResponse dataLogResponse,
  7: InterruptLogResponse interruptLogResponse,
  8: PowerLogResponse powerLogResponse,
  9: EventLogResponse eventLogResponse,
  10: DebuggerEventNotification debuggerEventNotification;
  11: CallStackResponse callStackResponse,
}

// -------------------------
// Client -> Debugger messages
// -------------------------

struct DataRequest {
  1: Source source,
  2: i64 startTimeNs,
  3: i64 endTimeNs,
}

struct InitializeRequest { }

// Enables the underlying data fetching for the specific source type.
struct EnableSourceTypeNotification {
  1: SourceType type,
  2: bool enable,
}

union ClientToDebuggerMessage {
  1: InitializeRequest initializeRequest,
  2: EnableSourceTypeNotification enableSourceTypeNotification,
  3: DataRequest dataRequest,
}

// Envelope-level sequencing:
// - seqId == 0 : uncorrelated message (notification)
// - seqId  > 0 : correlated request/response (client tracks seqId to match replies)

struct ClientToDebuggerEnvelope {
  1: i64 seqId,
  2: ClientToDebuggerMessage msg,
}

struct DebuggerToClientEnvelope {
  1: i64 seqId,
  2: DebuggerToClientMessage msg,
}
