---
name: algorithmic-discipline
description: Bắt buộc khai báo độ phức tạp Big-O và cấu trúc dữ liệu trước khi viết thuật toán.
---

# Algorithmic Discipline (Kỷ Luật Thuật Toán)

> **MỤC TIÊU**: Ngăn chặn rò rỉ hiệu năng bằng cách ép buộc tư duy thuật toán TRƯỚC KHI sinh ra code.

Mọi tác vụ yêu cầu duyệt mảng, tìm kiếm, sắp xếp, hoặc thao tác trên tập dữ liệu lớn đều phải tuân thủ nghiêm ngặt kỹ năng này.

## Quy tắc bắt buộc (Non-negotiable)

TRƯỚC KHI bạn viết bất kỳ hàm nào chứa vòng lặp (`for`, `while`, `map`, `reduce`, `filter`) hoặc đệ quy, bạn **BẮT BUỘC** phải chèn một khối bình luận `/* ALGO-CHECK ... */` (hoặc docstring tương tự tùy ngôn ngữ) ngay trên đầu hàm đó.

Khối bình luận này phải trả lời 3 câu hỏi sau:
1. **Time & Space Complexity**: Phân tích Big-O thời gian (Time) và không gian (Space).
2. **Data Structure Choice**: Cấu trúc dữ liệu nào được sử dụng để tối ưu? (Ví dụ: Tại sao dùng `Set` thay vì `Array.includes`?)
3. **Termination Proof**: Bằng chứng vòng lặp hoặc đệ quy sẽ dừng lại (không bị lặp vô hạn).

## Ví dụ đúng

```javascript
/* ALGO-CHECK
 * Time Complexity: O(N) vì chỉ duyệt mảng một lần. Space Complexity: O(N) để lưu HashSet.
 * Data Structure Choice: Sử dụng Set (seen) để tra cứu O(1) thay vì Array.includes O(N).
 * Termination Proof: Biến i tăng đều đặn 1 đơn vị mỗi vòng lặp cho đến khi i === arr.length.
 */
function findDuplicates(arr) {
  const seen = new Set();
  const duplicates = [];
  for (let i = 0; i < arr.length; i++) {
    if (seen.has(arr[i])) duplicates.push(arr[i]);
    seen.add(arr[i]);
  }
  return duplicates;
}
```

## Báo lỗi (Violation)
Không có hook nào kiểm tra thẻ `ALGO-CHECK` — `quality-gate.js` chỉ chạy prettier/biome/ruff/gofmt và luôn `process.exit(0)`, nên nó không thể chặn bất cứ thứ gì. Quy tắc này do agent tự giữ; người review là lớp thực thi duy nhất.
