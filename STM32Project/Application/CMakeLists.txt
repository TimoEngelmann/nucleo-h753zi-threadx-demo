cmake_minimum_required(VERSION 3.22)


#======================================================================================================================
# Change compiler settings of STM32CubeMX generated CMakeLists.txt in root:
#======================================================================================================================
# Compiler Standards:
set(CMAKE_C_STANDARD 23)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS ON)

# CMake support for ASM and C languages:
enable_language(C CXX ASM)

# Set target-specific C++ standard for the main executable:
set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES
    CXX_STANDARD 23
    CXX_STANDARD_REQUIRED ON
    CXX_EXTENSIONS ON
)


#======================================================================================================================
# Automatic adding c,h,cpp,hpp files in Application folder to CMake:
#======================================================================================================================
# Recursively collect all subdirectories under a given path
function(collect_all_subdirectories BASE_DIR OUT_VAR)
    file(GLOB_RECURSE ALL_ITEMS LIST_DIRECTORIES true "${BASE_DIR}/*")
    set(SUBDIRS "")
    foreach(ITEM ${ALL_ITEMS})
        if(IS_DIRECTORY "${ITEM}")
            list(APPEND SUBDIRS "${ITEM}")
        endif()
    endforeach()
    set(${OUT_VAR} "${SUBDIRS}" PARENT_SCOPE)
endfunction()

# Define source root directories
set(APPLICATION_SOURCE_DIR "${CMAKE_SOURCE_DIR}/Application")

# Collect source and header files
file(GLOB_RECURSE APPLICATION_CPP "${APPLICATION_SOURCE_DIR}/*.cpp")
file(GLOB_RECURSE APPLICATION_HPP "${APPLICATION_SOURCE_DIR}/*.hpp")
file(GLOB_RECURSE APPLICATION_C   "${APPLICATION_SOURCE_DIR}/*.c")
file(GLOB_RECURSE APPLICATION_H   "${APPLICATION_SOURCE_DIR}/*.h")
 
# Collect all subdirectories for include directories
collect_all_subdirectories(${APPLICATION_SOURCE_DIR} APPLICATION_SUBDIRS)
 
# For FILE_SET HEADERS, we only need the top-level directories
# CMake will automatically find headers in subdirectories
set(BASE_DIRS
    ${APPLICATION_SOURCE_DIR}
)
 
# Combine all directories for include paths (including root dirs and subdirs)
set(ALL_INCLUDE_DIRS
    ${APPLICATION_SOURCE_DIR}
    ${APPLICATION_SUBDIRS}
)
 
# Add sources and headers to the target
target_sources(${CMAKE_PROJECT_NAME} 
    PRIVATE # The source files are only used by the target itself.
        ${APPLICATION_CPP}
        ${APPLICATION_C}	
    PUBLIC # The source files are used by the target and also propagated to targets that link against it.
        FILE_SET HEADERS
        BASE_DIRS ${BASE_DIRS}
        FILES
            ${APPLICATION_HPP}
            ${APPLICATION_H}	
	
)


#======================================================================================================================
# Manual adding c,h,cpp,hpp files in Application folder to CMake:
#======================================================================================================================
# Manual adding of target_include_directories:
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add all collected directories to include paths
    ${ALL_INCLUDE_DIRS}
    # Add user defined include paths
)

# Manual adding of target_compile_definitions (project symbols or macros):
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols
)

# Manual adding of target_link_libraries:
target_link_libraries(${CMAKE_PROJECT_NAME}
    # Add user defined libraries
)


#======================================================================================================================
# Exclude files from build:
#======================================================================================================================
# Exclude app_threadx.c
#  The STM32CubeMx generator adds the file app_threadx.c to the sources of the project.
#  But we are using the methods in the C++ file application.cpp and do not need the app_threadx.c file.
#  This code filters out the app_threadx.c file from the list of sources.
set(EXCLUDED_SOURCES 
    ".*app_threadx\\.c$"
    # Add more files here as needed
)
get_target_property(CURRENT_SOURCES ${CMAKE_PROJECT_NAME} SOURCES)
if(CURRENT_SOURCES)
    # Filter out excluded files from the list of sources:
    set(FILTERED_SOURCES "")
    foreach(src IN LISTS CURRENT_SOURCES)
        set(EXCLUDE_FILE FALSE)
        # Check if current source matches any of the excluded patterns
        foreach(excluded_pattern IN LISTS EXCLUDED_SOURCES)
            if(src MATCHES "${excluded_pattern}")
                set(EXCLUDE_FILE TRUE)
                break()
            endif()
        endforeach()
        # Add file to filtered sources if not excluded
        if(NOT EXCLUDE_FILE)
            list(APPEND FILTERED_SOURCES "${src}")
        endif()
    endforeach()
    # Set the filtered sources back to the target
    set_target_properties(${CMAKE_PROJECT_NAME} PROPERTIES SOURCES "${FILTERED_SOURCES}") 
endif()


#======================================================================================================================
# Remove double flags from CMAKE_ASM_FLAGS, CMAKE_C_FLAGS and CMAKE_CXX_FLAGS:
#======================================================================================================================
# This occurs by including this Application/CMakeLists.txt.
# If someone knows why, please let me know.
string(REPLACE " " ";" _asmflags_list "${CMAKE_ASM_FLAGS}")
list(REMOVE_DUPLICATES _asmflags_list)
string(REPLACE ";" " " CMAKE_ASM_FLAGS "${_asmflags_list}")
string(REPLACE " " ";" _cflags_list "${CMAKE_C_FLAGS}")
list(REMOVE_DUPLICATES _cflags_list)
string(REPLACE ";" " " CMAKE_C_FLAGS "${_cflags_list}")
string(REPLACE " " ";" _cxxflags_list "${CMAKE_CXX_FLAGS}")
list(REMOVE_DUPLICATES _cxxflags_list)
string(REPLACE ";" " " CMAKE_CXX_FLAGS "${_cxxflags_list}")


#======================================================================================================================
# Debug Print out:
#======================================================================================================================
message(" ")
message(" ")
message("======================================================================================================================")
message("Debug Output:")
message("======================================================================================================================")

# Print out the FINAL_SOURCES:
get_target_property(FINAL_SOURCES ${CMAKE_PROJECT_NAME} SOURCES)
message(" ")
message("FINAL_SOURCES:")
if(FINAL_SOURCES)
    foreach(src IN LISTS FINAL_SOURCES)
        message("  ${src}")
    endforeach()
else()
    message("  No sources found")
endif()
message("")

# Print out the FINAL_INCLUDE_DIRS:
get_target_property(FINAL_INCLUDE_DIRS ${CMAKE_PROJECT_NAME} INCLUDE_DIRECTORIES)
message("FINAL_INCLUDE_DIRS:")
if(FINAL_INCLUDE_DIRS)
    foreach(dir IN LISTS FINAL_INCLUDE_DIRS)
        message("  ${dir}")
    endforeach()
else()
    message("  No include directories found")
endif()
message("")

# Print out the FINAL_COMPILE_DEFS:
get_target_property(FINAL_COMPILE_DEFS ${CMAKE_PROJECT_NAME} COMPILE_DEFINITIONS)
message("FINAL_COMPILE_DEFS:")
if(FINAL_COMPILE_DEFS)
    foreach(def IN LISTS FINAL_COMPILE_DEFS)
        message("  ${def}")
    endforeach()
else()
    message("  No compile definitions found")
endif()
message("")

# Print out the FINAL_LINK_LIBS:
get_target_property(FINAL_LINK_LIBS ${CMAKE_PROJECT_NAME} LINK_LIBRARIES)
message("FINAL_LINK_LIBS:")
if(FINAL_LINK_LIBS)
    foreach(lib IN LISTS FINAL_LINK_LIBS)
        message("  ${lib}")
    endforeach()
else()
    message("  No link libraries found")
endif()
message("")

# Print out the CMAKE_ASM_FLAGS, CMAKE_C_FLAGS and CMAKE_CXX_FLAGS:
message("CMAKE_ASM_FLAGS:" )
message("  ${CMAKE_ASM_FLAGS}")
message("")
message("CMAKE_C_FLAGS:" )
message("  ${CMAKE_C_FLAGS}")
message("")
message("CMAKE_CXX_FLAGS:" )
message("  ${CMAKE_CXX_FLAGS}")
message("")

message("ATTENTION: Following files are excluded from build:")
if(EXCLUDED_SOURCES)
    foreach(excluded_pattern IN LISTS EXCLUDED_SOURCES)
        message("  ${excluded_pattern}")
    endforeach()
else()
    message("  No excluded sources found")
endif()

message("")
