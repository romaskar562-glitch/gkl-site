/* Workbook exchange for a single reviewed ceiling zone. */
const CornerWorkbook = (() => {
  const sheetName = "Обмер углов";
  const version = "ГКЛ · обмер углов · 1";
  const headers = ["Угол", "Предложено, м", "Подтверждено, м", "Комментарий"];

  function template(fingerprint, zone) {
    return [
      [version, fingerprint],
      ["Страница PDF", zone.page, "Зона", zone.zone],
      ["Заполните столбец «Подтверждено, м». Введите 0, если угол следует исключить."],
      headers,
      ...zone.points.map((_, index) => [index + 1, zone.height_m, null, null]),
    ];
  }

  function create(xlsx, fingerprint, zone) {
    const workbook = xlsx.utils.book_new();
    const sheet = xlsx.utils.aoa_to_sheet(template(fingerprint, zone));
    sheet["!cols"] = [{wch: 24}, {wch: 20}, {wch: 23}, {wch: 36}];
    sheet["!merges"] = [{s: {r: 2, c: 0}, e: {r: 2, c: 3}}];
    xlsx.utils.book_append_sheet(workbook, sheet, sheetName);
    return workbook;
  }

  function read(xlsx, data, fingerprint, zones) {
    const workbook = xlsx.read(data, {type: "array", sheetRows: 50});
    const sheet = workbook.Sheets[sheetName];
    if (!sheet) throw new Error("В файле нет листа «Обмер углов».");
    const value = address => {
      const cell = sheet[address];
      if (cell?.f || cell?.F || cell?.t === "e") {
        throw new Error("Замените формулы и ошибки Excel числовыми значениями.");
      }
      return cell?.v;
    };
    if (value("A1") !== version || value("B1") !== fingerprint) {
      throw new Error("Excel относится к другому PDF или шаблону. Загрузите исходный PDF проекта.");
    }
    const zone = zones.find(item => item.page === value("B2") && item.zone === value("D2"));
    if (!zone) throw new Error("Страница или зона из Excel отсутствует в этом проекте.");
    if (headers.some((name, index) => value(String.fromCharCode(65 + index) + "4") !== name)) {
      throw new Error("В Excel изменены заголовки шаблона.");
    }
    const range = xlsx.utils.decode_range(sheet["!fullref"] || sheet["!ref"]);
    if (range.e.r >= 4 + zone.points.length) {
      throw new Error("Добавлены строки за пределами обмера зоны.");
    }
    const rows = zone.points.map((_, index) => {
      const row = 5 + index;
      if (value("A" + row) !== index + 1 || value("B" + row) !== zone.height_m) {
        throw new Error("Номера углов и предложенные высоты должны совпадать с шаблоном.");
      }
      const entered = value("C" + row);
      if (entered == null || String(entered).trim() === "") {
        throw new Error("Заполните подтверждённую длину угла " + (index + 1) + ".");
      }
      const normalized = String(entered).trim().replace(",", ".");
      if (!/^\d+(?:\.\d+)?$/.test(normalized)) {
        throw new Error("Для угла " + (index + 1) + " укажите длину в метрах или 0.");
      }
      const confirmed = Number(normalized);
      if (!Number.isFinite(confirmed) || confirmed > 100) {
        throw new Error("Проверьте, что длина угла указана в метрах.");
      }
      return {index: index + 1, proposed: zone.height_m, confirmed,
              comment: String(value("D" + row) ?? "").slice(0, 1000)};
    });
    return {zone, rows, total: rows.reduce((sum, row) => sum + row.confirmed, 0)};
  }

  return {create, read, sheetName};
})();
if (typeof module !== "undefined") module.exports = CornerWorkbook;

