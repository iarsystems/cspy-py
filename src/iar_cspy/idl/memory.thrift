
namespace cpp CSpy.Memory.Thrift
namespace java com.iar.cspy.memory

include "shared.thrift"

struct CSpyMemoryBlock
{
  1: binary data;

  // This binary contains a byte per each unit in the data binary
  2: binary status; 
}

// Do not call the service "Memory", since it will cause the thrift
// compiler to generate a header file called "Memory.h" which will
// (on windows) conflict with the system header file with the same name.
service CSpyMemory 
{
  binary readMemory(1: shared.Location location,
    2: i32 wordsize,
    3: i32 bitsize, 
    4: i32 count)
    throws (1: shared.CSpyException e)
  
  CSpyMemoryBlock readMemoryBlock(1: shared.Location location,
    2: i32 wordsize,
    3: i32 bitsize, 
    4: i32 count)
    throws (1: shared.CSpyException e)

  void writeMemory(1: shared.Location location,
    2: i32 wordsize,
    3: i32 bitsize, 
    4: i32 count, 
    5: binary buf)
    throws (1: shared.CSpyException e)
}
