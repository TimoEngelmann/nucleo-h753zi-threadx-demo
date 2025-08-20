/// ====================================================================================================================
/// \file       application.cpp
/// \brief      This file contains the creation of threads and timer, the thread functions including a demo simple application.
/// \details    This file contains the
///             - the creation of threads,
///             - the creation of timers,
///             - the thread functions including a demo simple application.
/// ====================================================================================================================


//======================================================================================================================
// MARK: Inclusions
//======================================================================================================================
#include "main.h" // Needed for the pin and port defines.
#include <cstdint>
#include <vector> // C++ vector for storing button timestamps. Just to test a C++ feature.
#include "stm32h7xx_hal_gpio.h"
#include "tx_api.h"
#include "app_threadx.h" // Contains the export declarations for App_ThreadX_Init() and MX_ThreadX_Init() functions.


//======================================================================================================================
// MARK: Globals
//======================================================================================================================

// --------------------------------------------------------------------------------------------------------------------
// Macros:
// --------------------------------------------------------------------------------------------------------------------

// --------------------------------------------------------------------------------------------------------------------
// Forward declarations:
// --------------------------------------------------------------------------------------------------------------------
/// Forward declaration of Main thread function:
void thrdFct_Main(ULONG thread_input);
/// Forward declaration of Main timer function:
void tmrFct_MainThreadTimer(ULONG timer_input);
/// Forward declaration of Background thread function:
void thrdFct_Background(ULONG thread_input);

// --------------------------------------------------------------------------------------------------------------------
// Typedefs:
// --------------------------------------------------------------------------------------------------------------------

// --------------------------------------------------------------------------------------------------------------------
// Enums:
// --------------------------------------------------------------------------------------------------------------------

// --------------------------------------------------------------------------------------------------------------------
// Variables / Objects:
// --------------------------------------------------------------------------------------------------------------------
// Application stuff:
// Some global counters to test the live watch feature of ST-Link debugger:
static uint32_t counterMain1 = 0;
static uint32_t counterMain2 = 0;
static uint32_t counterBackground = 0;
static uint32_t counterLD1 = 0;
static uint32_t counterLD2 = 0;
static uint32_t counterLD3 = 0;
static uint32_t counterButton = 0;
std::vector <uint32_t> buttonTimeStamps; // ATTENTION: This vector stores the data in heap memory. This should be avoided in real applications, as it can lead to memory fragmentation and other issues. It is used here just to test a C++ feature.


// --------------------------------------------------------------------------------------------------------------------
// Constants:
// --------------------------------------------------------------------------------------------------------------------
// Application stuff:
// Some constants to configure the application:
constexpr uint8_t counterMain1Max = 10;
constexpr uint8_t counterMain2Max = 100;


//======================================================================================================================
// MARK: Helper
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief   Converts milliseconds to timer ticks.
/// \details This function converts a duration in milliseconds to the equivalent duration in timer ticks.
///          Template parameter T specifies the return type (e.g., ULONG, uint32_t, uint16_t).
/// --------------------------------------------------------------------------------------------------------------------
template <typename T = uint32_t>
static inline T millisToTicks(T millis)
{
    constexpr uint64_t millisPerSec = 1000;
    if (millis == 0)
    {
        return static_cast<T>(0); // NOLINT(readability-braces-around-statements)
    }
    // Integer ceil-Division: (a + b - 1) / b
    uint64_t numerator = (uint64_t)millis * (uint64_t)TX_TIMER_TICKS_PER_SECOND;
    uint64_t ticks = (numerator + (millisPerSec - 1)) / millisPerSec;
    return static_cast<T>(ticks);
}


//======================================================================================================================
// MARK: Thread Config
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief    Handle for Main thread.
/// \details  This structure contains the thread control block.
/// --------------------------------------------------------------------------------------------------------------------
TX_THREAD thrdHdl_Main;

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Creates the Main Thread
/// \details    This function is called in App_ThreadX_Init() to create and configure the thread.
/// --------------------------------------------------------------------------------------------------------------------
void createThread_Main(VOID* ptrRtosMemoryPool)
{
    // Thread settings:
    TX_THREAD* thrdCtrlBlk = &thrdHdl_Main;          // Configure here the thread control block as handle to the thread. This must be declared in global area to use it e.g. in tx_thread_suspend. Use the pattern 'thrdHdl_[NameOfThread]'. Keep it short!
    CHAR thrdName[] = "thrd_Main";                   // Configure here a thread name. Use the pattern 'thrd_[NameOfThread]'. Keep it short!
    void (*thrdFctPtr)(ULONG param) = &thrdFct_Main; // Configure here the function name of the thread function. Use the pattern 'thrdFct_[NameOfThread]'. Keep it short!
    constexpr ULONG thrdParam = 0;                   // Configure here the parameter of the thread function.
    constexpr ULONG stackSize = 2 * 1024;            // Configure here the maximum size of the stack for this thread.
    constexpr UINT priority = 1;                     // Configure here initial thread priority (lower value = higher priority).
    constexpr UINT preemptionThreshold = priority;   // Configure here the preemption threshold (limits which threads can preempt this one).
    constexpr ULONG timeSlice = TX_NO_TIME_SLICE;    // Configure here the time slice value (TX_NO_TIME_SLICE = 0: disables time slicing for this thread).
    constexpr UINT autoStart = TX_AUTO_START;        // Configure here the auto-start option (thread starts automatically after creation).

    // Allocate the memory and get the stack pointer (ptrToStack):
    VOID* ptrToStack = nullptr; // This is needed for the following tx_thread_create() function.
    UINT result = tx_byte_allocate((TX_BYTE_POOL*)ptrRtosMemoryPool, &ptrToStack, stackSize, TX_NO_WAIT);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }

    // Create the Thread:
    result = tx_thread_create(thrdCtrlBlk, &thrdName[0], thrdFctPtr, thrdParam, ptrToStack, stackSize, priority, preemptionThreshold, timeSlice, autoStart);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }
}


/// --------------------------------------------------------------------------------------------------------------------
/// \brief    Handle for Background thread.
/// \details  This structure contains the thread control block.
/// --------------------------------------------------------------------------------------------------------------------
TX_THREAD thrdHdl_Background;

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Creates the Background Thread
/// \details    This function is called in App_ThreadX_Init() to create and configure the thread.
/// --------------------------------------------------------------------------------------------------------------------
void createThread_Background(VOID* ptrRtosMemoryPool)
{
    // --- Thread settings:
    TX_THREAD* thrdCtrlBlk = &thrdHdl_Background;           // Configure here the thread control block as handle to the thread. This must be declared in global area to use it e.g. in tx_thread_suspend. Use the pattern 'thrdHdl_[NameOfThread]'. Keep it short!
    CHAR thrdName[] = "thrd_Background";                    // Configure here a thread name. Use the pattern 'thrd_[NameOfThread]'. Keep it short!
    void (*thrdFctPtr)(ULONG param) = &thrdFct_Background;  // Configure here the function name of the thread function. Use the pattern 'thrdFct_[NameOfThread]'. Keep it short!
    constexpr ULONG thrdParam = 0;                          // Configure here the parameter of the thread function.
    constexpr ULONG stackSize = 2 * 1024;                   // Configure here the maximum size of the stack for this thread.
    constexpr UINT priority = 31;                           // Configure here initial thread priority (lower value = higher priority).
    constexpr UINT preemptionThreshold = priority;          // Configure here the preemption threshold (limits which threads can preempt this one).
    constexpr ULONG timeSlice = TX_NO_TIME_SLICE;           // Configure here the time slice value (TX_NO_TIME_SLICE = 0: disables time slicing for this thread).
    constexpr UINT autoStart = TX_DONT_START;               // Configure here the auto-start option (TX_AUTO_START thread starts automatically after creation or TX_DONT_START thread don't start).

    // --- Allocate the memory:
    VOID* ptrToStack = nullptr;
    UINT result = tx_byte_allocate((TX_BYTE_POOL*)ptrRtosMemoryPool, &ptrToStack, stackSize, TX_NO_WAIT);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }

    // --- Create the Thread:
    result = tx_thread_create(thrdCtrlBlk, &thrdName[0], thrdFctPtr, thrdParam, ptrToStack, stackSize, priority, preemptionThreshold, timeSlice, autoStart);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }
}


//======================================================================================================================
// MARK: Event Flags Config
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief    Event Flag Group for Main timer.
/// \details  This struct holds the state of the event flags.
/// --------------------------------------------------------------------------------------------------------------------
TX_EVENT_FLAGS_GROUP evtFlags_Main;

/// --------------------------------------------------------------------------------------------------------------------
/// \brief    Event flag for Main thread wake-up.
/// \details  This defines the bit position of the event flag.
/// --------------------------------------------------------------------------------------------------------------------
constexpr uint32_t evtFlag_Main_WakeUp = 0x00000001;

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Creates the event flags for main thread.
/// \details    This function is called in App_ThreadX_Init() to create and configure the event flags.
/// --------------------------------------------------------------------------------------------------------------------
void createEventFlags_Main()
{
    // --- Event Flags creation:
    TX_EVENT_FLAGS_GROUP* evtFlags = &evtFlags_Main; // Configure here the event flags group as handle to the event flags. This must be declared in global area to use it in application. Use the pattern 'evtFlags_[NameOfEventGroup]'. Keep it short!
    char evtGrpName[] = "evtGrp_Main";               // Configure here the name of the event group. Use the pattern 'evtGrp_[NameOfEventGroup]'. Keep it short!

    // Create the Event Flags Group:
    uint16_t result = tx_event_flags_create(evtFlags, &evtGrpName[0]);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }
}


//======================================================================================================================
// MARK: Timer Config
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief    Handle for Main timer.
/// \details  This structure contains the timer control block.
/// --------------------------------------------------------------------------------------------------------------------
TX_TIMER tmrHdl_Main;

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Creates the timer for main thread.
/// \details    This function is called in App_ThreadX_Init() to create and configure the timer.
/// --------------------------------------------------------------------------------------------------------------------
void createTimer_Main()
{
    // --- Timer creation:
    TX_TIMER* tmrCtrlBlk = &tmrHdl_Main;                      // Configure here the timer control block as handle to the timer. This must be declared in global area to use it in application. Use the pattern 'tmrHdl_[NameOfTimer]'. Keep it short!
    CHAR tmrName[] = "tmr_Main";                              // Configure here the name of the timer. Use the pattern 'tmr_[NameOfTimer]'. Keep it short!
    VOID (*tmrFuncPtr)(ULONG tmrId) = tmrFct_MainThreadTimer; // Configure here the function name of the timer function. Use the pattern 'tmrFct_[NameOfTimer]'. Keep it short!
    const ULONG initialDelayInMillis = 10;                    // Configure here the initial duration for the first timer expiration.
    const ULONG rescheduleDurationInMillis = 10;              // Configure here the duration for all timer expirations after the first. A zero for this parameter makes the timer a one-shot timer.

    // Create the Timer:
    UINT result = tx_timer_create(tmrCtrlBlk, &tmrName[0], tmrFuncPtr, 0, millisToTicks<ULONG>(initialDelayInMillis), millisToTicks<ULONG>(rescheduleDurationInMillis), TX_AUTO_ACTIVATE);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }
}


//======================================================================================================================
// MARK: Callback Handler
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Callback function for handling stack overflow errors in a ThreadX thread.
/// \details    This function is called when a stack overflow is detected in a ThreadX thread.
///             It disables interrupts and end in an endless loop.
///             This should be replaced with a proper error handling mechanism.
/// --------------------------------------------------------------------------------------------------------------------
void stack_error_handler(TX_THREAD __attribute__((unused)) * thread)
{
    // Disable interrupts:
    UINT old_posture = tx_interrupt_control(TX_INT_DISABLE);

    // TODO: Replace it with an error handling mechanism!
    while (true)
    {
    };

    // Re-enable interrupts (but this will not happen, as the error handler enters an infinite loop):
    tx_interrupt_control(old_posture);
}


/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Callback function for initializing the ThreadX kernel.
/// \details    This function starts the ThreadX kernel by calling the `tx_kernel_enter` function.
///             It is the entry point for initializing the RTOS and should be called during system startup.
///             ATTENTION: This function is originally placed in the app_threadX.c file, generated by STM32CubeMX.
/// --------------------------------------------------------------------------------------------------------------------
void MX_ThreadX_Init()
{
    tx_kernel_enter();
}


/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Callback function for initializing the ThreadX application.
/// \details    This function sets up the necessary threads, timers, and error handlers for the ThreadX RTOS.
///             ATTENTION: This function is originally placed in the app_threadX.c file, generated by STM32CubeMX.
/// --------------------------------------------------------------------------------------------------------------------
UINT App_ThreadX_Init(VOID* memory_ptr)
{
    // --- Create threads and timers:
    createThread_Background(memory_ptr);
    //tx_thread_suspend(&thrdHdl_Background); // Suspend the background direct thread until it is resumed by the main thread.
    createThread_Main(memory_ptr);
    createEventFlags_Main();
    createTimer_Main();

    // Register the stack error handler
    UINT result = tx_thread_stack_error_notify(stack_error_handler);
    if (result != TX_SUCCESS)
    {
        // TODO: Replace it with an error handling mechanism!
        while (true)
        {
        };
    }

    // Return:
    return TX_SUCCESS;
}


//======================================================================================================================
// MARK: Timer Functions
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Timer function for the main thread.
/// \details    This function is called when the timer expires.
/// --------------------------------------------------------------------------------------------------------------------
void tmrFct_MainThreadTimer(ULONG __attribute__((unused)) timer_input)
{
    // Set an event flag to wake up the main thread for synchronized execution:
    tx_event_flags_set(&evtFlags_Main, evtFlag_Main_WakeUp, TX_OR);
}


//======================================================================================================================
// MARK: Thread Functions
//======================================================================================================================

/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Main thread function.
/// \details    This function implements the behavior of the main application thread.
///             It continuously waits for a timer event flag. After each event the application code is run once.
///             This results in a time-synchronized execution of the application code every [periodInMillsecs].
///             See configured in createTimer_Main().
/// --------------------------------------------------------------------------------------------------------------------
void thrdFct_Main(ULONG __attribute__((unused)) thread_input)
{
    // --- Init Application:
    // Place here initialization stuff that needs to be done before starting the threads.
    // Initialize LED1 (turn on at startup)
    HAL_GPIO_WritePin(LED1_Green_GPIO_Port, LED1_Green_Pin, GPIO_PIN_SET);
    HAL_GPIO_WritePin(LED2_Orange_GPIO_Port, LED2_Orange_Pin, GPIO_PIN_SET);
    HAL_GPIO_WritePin(LED3_Red_GPIO_Port, LED3_Red_Pin, GPIO_PIN_SET);

    // Start now the background thread:
    tx_thread_resume(&thrdHdl_Background);

    // Infinite loop:
    for (;;)
    {
        { // --- Main Application:
          // Place here the cyclic application stuff.
          // It runs every [periodInMillsecs] see configured in createTimer_Main().

            // Demo code to blink the LD1:
            counterMain1++;
            if (counterMain1 >= counterMain1Max)
            {
                counterMain1 = 0;
                counterLD1++;
                HAL_GPIO_TogglePin(LED1_Green_GPIO_Port, LED1_Green_Pin);
            }

            // Demo code to blink the LD2:
            counterMain2++;
            if (counterMain2 >= counterMain2Max)
            {
                counterMain2 = 0;
                counterLD2++;
                HAL_GPIO_TogglePin(LED2_Orange_GPIO_Port, LED2_Orange_Pin);
            }
        }

        // Wait for the next timer event flag:
        // This implements the time synchronized behavior of the main thread.
        ULONG receivedEvtFlags = 0;
        tx_event_flags_get(&evtFlags_Main, evtFlag_Main_WakeUp, TX_OR_CLEAR, &receivedEvtFlags, TX_WAIT_FOREVER);
    }
}


/// --------------------------------------------------------------------------------------------------------------------
/// \brief      Background thread function.
/// \details    This function implements the behavior of the background thread.
///             It continuously executes in an infinite loop with lowest priority (31) to use the free processing time.
/// --------------------------------------------------------------------------------------------------------------------
void thrdFct_Background(ULONG __attribute__((unused)) thread_input)
{    
    // Infinite loop:
    for (;;)
    {
        { // Background Application:
          // Place here the background stuff.

            // Increment demo counter:
            counterBackground++;

            // Check if button B1 is pressed (active high)
            // TODO: Insert debouncing.
            if (HAL_GPIO_ReadPin(Button1_Blue_GPIO_Port, Button1_Blue_Pin) == GPIO_PIN_SET)
            {
                if (HAL_GPIO_ReadPin(LED3_Red_GPIO_Port, LED3_Red_Pin) == GPIO_PIN_RESET)
                {
                    counterButton++;
                    counterLD3++;
                    buttonTimeStamps.push_back(counterBackground);
                    HAL_GPIO_WritePin(LED3_Red_GPIO_Port, LED3_Red_Pin, GPIO_PIN_SET);
                }
            }
            else
            {
                HAL_GPIO_WritePin(LED3_Red_GPIO_Port, LED3_Red_Pin, GPIO_PIN_RESET);
            }
        }
    }
}
