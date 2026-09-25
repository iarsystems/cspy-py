
namespace cpp LogService.Thrift
namespace java com.iar.logservice

include "shared.thrift"

const string LOGSERVICE_ID = "com.iar.thrift.service.logservice";

enum LogSeverity
{
    kDebug     = -1,
    kUser      = 0,
    kMinorInfo = 1,
    kInfo      = 2,
    kWarning   = 3,
    kError     = 4,
    kAlert     = 5,
    kSuper     = 6
}

struct SrcPos
{
  1: i32 row,
  2: i32 col,
  3: bool valid,
}

/**
 * An entry in a log, matches IfLogServer::Entry
 */
struct LogEntry
{
  1: string category,
  2: string sender,
  3: string text,
  4: LogSeverity severity,
  5: string path,
  6: SrcPos srcPos,
  7: SrcPos srcEndPos,
  8: i64 timestamp,
  9: i64 entryId,
  10: bool isSubEntry,
}

/**
 * A receiver of LogEntry:s. This is a thrift clone of the IfLogServer
 * interface. Registering this service and starting the ThriftLogForwarder
 * service in the IDE platform lets you receive everything logged by the IDE
 * platform.
 */
service LogService extends shared.HeartbeatService {

  void addCategory(1: string category)
  void removeCategory(1: string category)

  void startSession(1: string category)

  void postLogEntry(1: LogEntry entry)

}
