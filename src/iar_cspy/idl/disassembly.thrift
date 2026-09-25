// Thrift service definition for the Disassembly Thrift service //////

namespace cpp CSpy.Disassembly.Thrift
namespace java com.iar.cspy.disassembly

include "shared.thrift"

/** Unique identifier for the Disassembly service */
const string DISASSEMBLY_SERVICE = "disassembly";

/** A disassembly operation result, matching a memory location with instructions */
struct DisassembledLocation {
   1: shared.Location location,
   2: list<string> instructions,
   3: string _function,
   4: i64 offset 
}

/** The Disassembly service retrieves assembler instructions for a specific memory range */
service Disassembly extends shared.HeartbeatService {
  /** Retrieve a list of disassembled instructions for a specific memory range.*/
  list<DisassembledLocation> disassembleRange(1: shared.Location _from, 
    2: shared.Location _to,
    3: shared.ContextRef context)
    throws (1: shared.CSpyException e);

  /** This method is currently not implemented and will fail if called. */
  list<DisassembledLocation> disassembleLines(1: shared.Location _from, 
    2: i32 numLines,
    3: shared.ContextRef context)
    throws (1: shared.CSpyException e);           
}
