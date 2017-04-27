#ifndef TROLL_FILE_H
#define TROLL_FILE_H

#include <string>
#include <fstream>

#include "FileContent.h"

class File {

    FileContent content_;

public:
    File() {}
    File(const std::string& path) {
        std::ifstream f(path);
        if (f.good()) {
            std::string line;
            while (std::getline(f, line)) {
                content_.appendLine(line);
            }
        } else {
            abort();
        }
    }

    FileContent& content() {
        return content_;
    }
};


#endif //TROLL_FILE_H
