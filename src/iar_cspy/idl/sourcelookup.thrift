//
// Debug info lookup service to convert Location to SourceLocation
//

namespace cpp CSpy.SourceLookup.Thrift
namespace java com.iar.cspy.sourcelookup

include "shared.thrift"

const string SOURCE_LOOKUP_SERVICE = "sourcelookup";
const shared.ServiceVersion SOURCE_LOOKUP_VERSION = {
  "major": 1, // Increment for breaking changes
  "minor": 0  // Increment for non-breaking changes
};

service SourceLookup extends shared.HeartbeatService {
  /** Get the service version hosted by the server. */
  shared.ServiceVersion GetServiceVersion();


  list<shared.SourceRange> getSourceRanges(1: shared.Location loc)
    throws (1: shared.CSpyException e)            

}