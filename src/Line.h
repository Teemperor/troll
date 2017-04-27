#ifndef TROLL_LINE_H
#define TROLL_LINE_H

#include <string>

class Line {

    std::string content_;

public:
    Line() {
    }
    Line(const std::string& content) : content_(content) {
    }

    const std::string& content() const {
        return content_;
    }
};


#endif //TROLL_LINE_H
