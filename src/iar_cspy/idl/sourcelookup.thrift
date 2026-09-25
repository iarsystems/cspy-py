//
// Debug info lookup service to convert Location to SourceLocation
//

namespace cpp CSpy.SourceLookup.Thrift
namespace java com.iar.cspy.sourcelookup

include "shared.thrift"

const string SOURCE_LOOKUP_SERVICE = "sourcelookup";

service SourceLookup extends shared.HeartbeatService {

  list<shared.SourceRange> getSourceRanges(1: shared.Location loc)
    throws (1: shared.CSpyException e)            

}