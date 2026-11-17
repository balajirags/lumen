package com.example.inventory.repository;

import java.util.List;

@Repository
public interface ItemRepository {

    List<String> findAllItems();

    void save(String name);
}
