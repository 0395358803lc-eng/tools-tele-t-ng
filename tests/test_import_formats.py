from openpyxl import Workbook

from telegram_phone_number_checker.main import _read_phone_file


def test_cli_reads_xlsx_phone_column(tmp_path):
    path = tmp_path / "phones.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "phone"])
    sheet.append(["A", "+84911111111"])
    sheet.append(["B", "+84922222222"])
    workbook.save(path)
    workbook.close()
    assert _read_phone_file(str(path)) == ["+84911111111", "+84922222222"]
