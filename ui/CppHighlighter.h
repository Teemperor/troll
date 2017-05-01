#ifndef TROLL_CPPHIGHLIGHTER_H
#define TROLL_CPPHIGHLIGHTER_H

#include "ColorString.h"
#include <string>
#include <set>

class CppHighlighter {

  struct Token {
    std::string content;
    int start;
    int end;

    enum class Type {
      Comment,
      Ident,
      Number,
      String,
      Operator,
      Bracket,
      Keyword,
      PPDirective,
      Space,
      Colon,
      Unknown
    };
    Type type;
  };

  static bool isCharUS(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
  }

  static bool isNum(char c) {
    return c >= '0' && c <= '9';
  }

  static bool isQuote(char c) {
    return c == '"' || c == '\'';
  }

  static Token makeToken(const std::string &line, int offset,
                         bool hadNonSpaceToken) {
    Token result;
    result.start = offset;
    char c = line.at(offset);

    assert(!line.empty());

    if (!hadNonSpaceToken && c == '#') {
      result.type = Token::Type::PPDirective;
      result.end = line.size();
      result.content = line.substr(offset);
      return result;
    }

    if (c == '/') {
      if (offset + 1 < line.size()) {
        if (line.at(offset + 1) == '/') {
          result.content = line.substr(offset);
          result.type = Token::Type::Comment;
          result.end = line.size();
          return result;
        }
      }
    }

    std::string operators = "<>+-/*~";

    if (operators.find(c) != operators.npos) {
      result.content = std::string(1, c);
      result.end = offset + 1;
      result.type = Token::Type::Operator;
      return result;
    }

    std::string brackets = "(){}[]";

    if (brackets.find(c) != brackets.npos) {
      result.content = std::string(1, c);
      result.end = offset + 1;
      result.type = Token::Type::Bracket;
      return result;
    }

    std::string spaces = " \t";

    if (spaces.find(c) != spaces.npos) {
      result.content = std::string(1, c);
      result.end = offset + 1;
      result.type = Token::Type::Space;
      return result;
    }


    if (c == ':') {
      result.content = std::string(1, c);
      result.end = offset + 1;
      result.type = Token::Type::Colon;
      return result;
    }


    static std::set<std::string> Keywords =
        {"static", "operator", "bool", "int", "double",
         "float", "public", "private", "class", "struct",
         "long", "void", "static", "typedef", "namespace",
         "enum", "const", "mutable", "unsigned", "explicit",
         "return", "false", "true", "char", "if", "while", "for",
         "assert"};

    if (isCharUS(c)) {
      result.content = std::string(1, c);
      offset++;

      while (offset + 1 < line.size()) {
        c = line.at(offset);
        if (isCharUS(c) || isNum(c)) {
          result.content += c;
        } else {
          break;
        }
        offset++;
      }
      result.end = offset;

      if (Keywords.find(result.content) != Keywords.end()) {
        result.type = Token::Type::Keyword;
      } else {
        result.type = Token::Type::Ident;
      }

      return result;
    }

    if (isNum(c)) {
      result.content = std::string(1, c);
      result.end = offset + 1;
      result.type = Token::Type::Number;
      return result;
    }

    if (isQuote(c)) {
      result.content = std::string(1, c);
      offset++;

      bool lastWasBackslash = false;
      while (offset + 1 < line.size()) {
        c = line.at(offset);
        if (lastWasBackslash || !isQuote(c)) {
          result.content += c;
        } else {
          break;
        }
        lastWasBackslash = (c == '\\');
        offset++;
      }

      result.end = offset + 1;
      result.type = Token::Type::String;
      return result;
    }


    result.content = std::string(1, c);
    result.end = offset + 1;
    result.type = Token::Type::Unknown;
    return result;
  }

  static std::vector<Token> tokenize(const std::string &line) {
    std::vector<Token> result;

    bool hadNonSpaceToken = false;
    int offset = 0;
    while (offset < line.length()) {
      Token t = makeToken(line, offset, hadNonSpaceToken);
      offset = t.end;
      if (t.type != Token::Type::Space)
        hadNonSpaceToken = true;
      result.push_back(t);
    }

    return result;
  }

public:

  static ColorString highlight(const std::string &line) {
    auto tokens = tokenize(line);
    ColorString str(line);

    for (auto &t : tokens) {
      int color = 0;
      switch (t.type) {
        case Token::Type::Comment:
          color = 5;
          break;
        case Token::Type::Bracket:
          color = 7;
          break;
        case Token::Type::Operator:
          color = 4;
          break;
        case Token::Type::PPDirective:
          color = 5;
          break;
        case Token::Type::Keyword:
          color = 6;
          break;
        case Token::Type::Number:
          color = 0;
          break;
        case Token::Type::String:
          color = 5;
          break;
        case Token::Type::Colon:
          color = 5;
          break;
        default:
          break;
      }

      str.colorize(t.start, t.end, color);
    }

    return str;

  }

};


#endif //TROLL_CPPHIGHLIGHTER_H
