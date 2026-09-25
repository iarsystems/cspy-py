namespace cpp CSpy.Breakpoints.Thrift
namespace java com.iar.cspy.breakpoints

include "shared.thrift"

const string BREAKPOINTS_SERVICE = "breakpoints";

service Breakpoints extends shared.HeartbeatService {
  list<shared.Breakpoint> getBreakpoints()
    throws (1: shared.CSpyException e)            

  shared.Breakpoint getBreakpoint(1: i32 id)
    throws (1: shared.CSpyException e)            

  shared.Breakpoint setBreakpointFromDescriptor(1: string descriptor)
    throws (1: shared.CSpyException e)            
    
  shared.Breakpoint setBreakpointOnUle(1: string ule, 2: shared.AccessType accessType) 
    throws (1: shared.CSpyException e)
  
  shared.Breakpoint setBreakpointOnUleWithCategory(1: string ule, 
    2: shared.AccessType accessType, 3: string categoryId) 
    throws (1: shared.CSpyException e)
    
  bool enableBreakpoint(1: i32 id,  2: bool enable)
    throws (1: shared.CSpyException e)  
    
  bool removeBreakpoint(1: i32 id)
    throws (1: shared.CSpyException e)  

  list<shared.Breakpoint> getRecentlyHitBreakpoints()
    throws (1: shared.CSpyException e)            

}