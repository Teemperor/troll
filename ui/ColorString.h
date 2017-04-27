#ifndef TROLL_COLORSTRING_H
#define TROLL_COLORSTRING_H

#include <string>
#include <ncurses.h>
#include <vector>
#include <utility>
#include <cassert>

class ColorString {

    std::string content;
    int cursor = -1;
    std::vector<int> colors;
public:
    ColorString() {}
    ColorString(const std::string& c) : content(c) {
        colors.resize(c.size() + 1, 0);
        content.push_back(' ');
    }

    void colorize(int start, int end, int color) {
        for (int i = start; i < end; i++) {
            if (i >= colors.size())
                break;
            colors[i] = color;
        }
    }

    void setCursor(int c) {
        cursor = c;
    }

    void display(int x, int y, int fillup) {
        if (content.empty())
            return;

        int color = -1;
        for (int i = 0; i < colors.size(); ++i) {
            if (colors.at(i) != color) {
                if (color != -1) {
                    attroff(COLOR_PAIR(color));
                }
                color = colors.at(i);
                attron(COLOR_PAIR(color));
            }
            if (i == cursor)
                attron(A_REVERSE);
            mvprintw(y, x + i, "%c", content.at(i));
            if (i == cursor)
                attroff(A_REVERSE);
        }
        attroff(COLOR_PAIR(color));
        if (fillup - colors.size() <= 0)
            return;
        std::string fill;
        fill.resize(fillup - colors.size(), ' ');
        mvprintw(y, x + colors.size(), "%s", fill.c_str());
    }

};


#endif //TROLL_COLORSTRING_H
