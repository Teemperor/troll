#ifndef TROLL_FILEVIEW_H
#define TROLL_FILEVIEW_H

#include <curses.h>

#include "File.h"
#include "ColorString.h"

class FileView {

    long scrollPos_ = 0;

    File* file_ = nullptr;

    int x_ = 0;
    int y_ = 0;
    int width_ = 20;
    int height_ = 20;

    ColorString highlight(const std::string& line) {
        ColorString str(line);

        if (line.find('#') == 0)
            str.colorize(0, line.size(), 3);
        else if (line.find("//") == 0)
            str.colorize(0, line.size(), 5);
        else {
            for (int i = 0; i < line.size(); i++) {
                char c = line.at(i);
                if (c == '+' || c == '<')
                    str.colorize(i, i + 1, 4);
            }
        }


        return str;
    }

public:
    FileView(int x, int y, int width, int height)
            : x_(x), y_(y), width_(width), height_(height) {
    }

    void setFile(File* file) {
        file_ = file;
        scrollPos_ = 0;
    }

    void scrollView(long diff) {
        scrollPos_ += diff;
        if (scrollPos_ < 0)
            scrollPos_ = 0;
    }

    void display() {
        for (int i = 0; i < height_; i++) {
            int lineIndex = i + scrollPos_;
            if (lineIndex < file_->content().linecount()) {
                std::string line = file_->content().line(lineIndex).content();
                ColorString str = highlight(line);
                str.display(x_, i, 180);
            } else {
                std::string line;
                line.resize(180, ' ');
                mvprintw(i, y_, "%s", line.c_str());
            }
        }
    }

};


#endif //TROLL_FILEVIEW_H
