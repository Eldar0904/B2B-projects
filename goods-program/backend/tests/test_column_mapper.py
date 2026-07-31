from app.services.column_mapper import auto_map_columns


def test_maps_master_catalog_headers():
    headers = [
        "Код",
        "Наименование",
        "Описание",
        "Единица измерения",
        "Класс груза",
        "Масса брутто, кг",
        "Сметная\xa0цена, тенге",
        "поисковый текст",
    ]
    mapping, unmapped = auto_map_columns(headers)
    assert mapping["external_id"] == "Код"
    assert mapping["product_name"] == "Наименование"
    assert mapping["description"] == "Описание"
    assert mapping["unit"] == "Единица измерения"
    assert mapping["freight_class"] == "Класс груза"
    assert mapping["gross_weight_kg"] == "Масса брутто, кг"


def test_maps_destination_headers_with_different_naming():
    headers = [
        "Код",
        "Наименование товара",
        "Описание",
        "Цена с НДС, в тенге",
        "SUM из Кол-во",
        "поисковый текст",
        "поисковая маска",
    ]
    mapping, unmapped = auto_map_columns(headers)
    assert mapping["product_name"] == "Наименование товара"
    assert mapping["price"] == "Цена с НДС, в тенге"
    assert mapping["quantity"] == "SUM из Кол-во"


def test_english_headers_also_map():
    headers = ["ID", "Product Name", "Category", "Description", "Brand", "Model", "Unit", "Price"]
    mapping, unmapped = auto_map_columns(headers)
    assert mapping["product_name"] == "Product Name"
    assert mapping["price"] == "Price"
    assert mapping["brand"] == "Brand"
    assert mapping["model"] == "Model"


def test_each_header_used_at_most_once():
    headers = ["Наименование", "Наименование товара"]
    mapping, unmapped = auto_map_columns(headers)
    used = list(mapping.values())
    assert len(used) == len(set(used))
