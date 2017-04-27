#ifndef TROLL_FILECONTENT_H
#define TROLL_FILECONTENT_H

#include <deque>
#include "Line.h"

class FileContent {
    std::deque<Line> lines_;

public:
    FileContent() {
    }

    void appendLine(const std::string& line) {
        lines_.push_back(Line(line));
    }

    Line& line(size_t index) {
        return lines_.at(index);
    }

    size_t linecount() const {
        return lines_.size();
    }
};


#endif //TROLL_FILECONTENT_H
