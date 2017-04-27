#ifndef TROLL_COMMANDBAR_H
#define TROLL_COMMANDBAR_H

#include <string>
#include <deque>
#include <curses.h>
#include "ColorString.h"

class CommandBar {

    int x_;
    int y_;
    int width_;

    bool activated = false;
    int position = 0;

    std::deque<std::string> pastCommands_;

    std::string contents;

    void fixCursor() {
        position = std::max(0, position);
        position = std::min((int) contents.size(), position);
    }

public:
    CommandBar(int x, int y, int width)
      : x_(x), y_(y), width_(width) {
    }

    void inputCharacter(char c) {
        contents.insert(contents.begin() + position, c);
        position++;
        fixCursor();
    }

    void moveCursor(int diff) {
        position += diff;
        fixCursor();
    }

    void delChar() {
        if (position == contents.size()) {
            if (!contents.empty())
                contents.resize(contents.size() - 1);
        } else if (position < contents.size()) {
            contents.erase(position, 1);
        }
        position--;
        fixCursor();
    }

    void activate() {
        activated = true;
    }

    bool active() const {
        return activated;
    }

    void execute() {
        pastCommands_.push_back(contents);
        contents.clear();
        position = 0;
    }

    void display() {
        if (!activated)
            return;
        std::string preline = "run:";
        mvprintw(y_ - 1, x_, "%s", preline.c_str());

        std::string line = contents;

        if (!contents.empty())
            for (const std::string& prev : pastCommands_) {
                if (prev.find(line) == 0) {
                    line = prev;
                    break;
                }
            }


        ColorString str(line);
        str.setCursor(position);
        str.colorize(contents.size(), 1000, 1);
        str.display(x_ + 5, y_ - 1, 160);
    }
};


#endif //TROLL_COMMANDBAR_H
