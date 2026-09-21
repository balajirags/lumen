package com.example.inventory.service;

import com.example.inventory.notify.Notifier;
import com.example.inventory.repository.ItemRepository;

import java.util.List;

@Service
public class InventoryService {

    private final ItemRepository itemRepository;
    private final Notifier notifier;

    public InventoryService(ItemRepository itemRepository, Notifier notifier) {
        this.itemRepository = itemRepository;
        this.notifier = notifier;
    }

    public List<String> findAll() {
        return itemRepository.findAllItems();
    }

    public void addItem(String name) {
        itemRepository.save(name);
        notifier.notify("Item added: " + name);
    }
}
