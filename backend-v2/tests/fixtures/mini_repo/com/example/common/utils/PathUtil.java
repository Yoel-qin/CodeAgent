package com.example.common.utils;

public class PathUtil {

    public static String normalize(String raw) {
        return raw == null ? "" : raw.replace('\\', '/');
    }
}
