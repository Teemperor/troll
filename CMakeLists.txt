cmake_minimum_required(VERSION 3.7)
project(troll)

set(CMAKE_CXX_STANDARD 11)

include_directories(src)
include_directories(ui)

set(SOURCE_FILES main.cpp src/Line.cpp src/Line.h src/FileContent.cpp src/FileContent.h src/File.cpp src/File.h ui/FileView.cpp ui/FileView.h ui/InputController.cpp ui/InputController.h ui/CommandBar.cpp ui/CommandBar.h ui/ColorString.cpp ui/ColorString.h)
add_executable(troll ${SOURCE_FILES})
target_link_libraries(troll ncurses)